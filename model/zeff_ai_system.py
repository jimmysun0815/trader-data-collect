#!/usr/bin/env python3
"""
MLP zeff AI: 6 维特征 -> delta_odds_rel (回归)，输出连续 zeff。
子命令 train / predict；与 zeff_ml_train 共用同一套 train_data 列与 high-acc 逻辑。

用已有 .pth + .scaler.joblib 生成 zeff_predicted_15m.csv（回测用）:
  从项目根目录:
    python training/zeff_ai_system.py predict \\
      --model real_market/trade/model/zeff_mlp_model.pth \\
      --input-csv training/train_data_15m.csv \\
      --output-csv training/zeff_predicted_15m.csv
  scaler 默认与 .pth 同目录同 stem 的 .scaler.joblib，无需单独指定。
输入 CSV 需含 6 维特征: zscore, raw_score, delta_15s_pct, btc_vol_60s, raw_score_ema, hour_of_day（缺列会填 0）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

# 固定 6 维特征（含 hour_of_day，由 t_unix 推导）
FEATURES = ["zscore", "raw_score", "delta_15s_pct", "btc_vol_60s", "raw_score_ema", "hour_of_day"]
TARGET_REG = "delta_odds_rel"
HIGH_ACC_HOURS_UTC = {0, 3, 4, 5, 6, 7, 12, 13, 19, 20, 23}


def _parse_hidden_sizes(s: str) -> list[int]:
    """Parse '1024 512 256 128' -> [1024, 512, 256, 128]."""
    return [int(x) for x in s.strip().split() if x]


DEFAULT_HIDDEN_SIZES = [1024, 512, 256, 128]


class ZeffMLP(nn.Module):
    """MLP: input_dim -> hidden_sizes[0] -> ... -> 1, Linear -> BatchNorm1d -> ReLU -> Dropout."""

    def __init__(self, input_dim: int = 6, hidden_sizes: list[int] | None = None, dropout_p: float = 0.3):
        super().__init__()
        hidden_sizes = hidden_sizes or DEFAULT_HIDDEN_SIZES
        sizes = [input_dim] + list(hidden_sizes)
        self.fcs = nn.ModuleList()
        self.bns = nn.ModuleList()
        for i in range(len(sizes) - 1):
            self.fcs.append(nn.Linear(sizes[i], sizes[i + 1]))
            self.bns.append(nn.BatchNorm1d(sizes[i + 1]))
        self.out = nn.Linear(sizes[-1], 1)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(dropout_p)
        self._n_hidden = len(hidden_sizes)

    def forward(self, x):
        for i in range(self._n_hidden):
            x = self.fcs[i](x)
            x = self.bns[i](x)
            x = self.relu(x)
            if i < self._n_hidden - 1:
                x = self.dropout(x)
        return self.out(x)


def _prepare_data(
    df: pd.DataFrame,
    *,
    clip_zscore: tuple[float, float] = (-5.0, 5.0),
    clip_target: tuple[float, float] = (-10.0, 10.0),
    high_acc_only: bool = False,
):
    """与 zeff_ml_train 对齐：dropna/clip/high-acc，构造 6 维 X 与 y。返回 (X, y, features)。"""
    df = df.copy()
    if high_acc_only and "t_unix" in df.columns:
        df["hour"] = pd.to_datetime(df["t_unix"], unit="s", utc=True).dt.hour
        df = df[df["hour"].isin(HIGH_ACC_HOURS_UTC)].reset_index(drop=True)
        print(f"过滤 high acc 时段后剩余行数: {len(df)}", flush=True)
    df = df.dropna(subset=["zscore", TARGET_REG]).copy()
    df["zscore"] = df["zscore"].clip(*clip_zscore)
    df[TARGET_REG] = df[TARGET_REG].clip(*clip_target)
    if "hour_of_day" not in df.columns and "t_unix" in df.columns:
        df["hour_of_day"] = pd.to_datetime(df["t_unix"], unit="s", errors="coerce").dt.hour
    for c in FEATURES:
        if c not in df.columns:
            df[c] = 0.0
    X = df[FEATURES].copy().fillna(0.0)
    y = df[TARGET_REG].values
    return X, y, list(FEATURES)


def cmd_train(args: argparse.Namespace) -> int:
    """Train MLP on train_data CSV; time-split 80/20; early stop; save .pth + optional scaler."""
    from torch.utils.data import DataLoader, TensorDataset

    train_csv = Path(args.train_csv)
    if not train_csv.exists():
        print(f"Error: train CSV not found: {train_csv}", file=sys.stderr)
        return 1
    df = pd.read_csv(train_csv)
    high_acc_only = getattr(args, "high_acc_only", False)
    clip_zscore = (float(args.clip_zscore_min), float(args.clip_zscore_max))
    clip_target = (float(args.clip_target_min), float(args.clip_target_max))
    X, y, features = _prepare_data(df, high_acc_only=high_acc_only, clip_zscore=clip_zscore, clip_target=clip_target)
    if len(X) == 0:
        print("Error: no rows after dropna.", file=sys.stderr)
        return 1

    train_size = int(len(X) * args.train_ratio)
    X_train, X_val = X.iloc[:train_size], X.iloc[train_size:]
    y_train, y_val = y[:train_size], y[train_size:]

    scaler = None
    if args.scale:
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        X_train = pd.DataFrame(
            scaler.fit_transform(X_train),
            columns=features,
            index=X_train.index,
        )
        X_val = pd.DataFrame(
            scaler.transform(X_val),
            columns=features,
            index=X_val.index,
        )

    from sklearn.preprocessing import StandardScaler
    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train.reshape(-1, 1)).ravel()
    y_val_scaled = y_scaler.transform(y_val.reshape(-1, 1)).ravel()

    t_start = time.time()
    device = _resolve_device(args.device)
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"使用设备: {device}, GPU 可用: {torch.cuda.is_available()}, 设备名: {gpu_name}", flush=True)
    X_train_t = torch.tensor(X_train.values, dtype=torch.float32)
    y_train_t = torch.tensor(y_train_scaled.astype(np.float32), dtype=torch.float32).unsqueeze(1)
    X_val_t = torch.tensor(X_val.values, dtype=torch.float32)
    y_val_scaled_arr = y_val_scaled.astype(np.float32)

    train_ds = TensorDataset(X_train_t, y_train_t)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    input_dim = len(features)
    hidden_sizes = _parse_hidden_sizes(args.hidden_sizes)

    model = ZeffMLP(input_dim=input_dim, hidden_sizes=hidden_sizes, dropout_p=args.dropout_p).to(device)

    def _init_weights(m: nn.Module) -> None:
        if isinstance(m, nn.Linear):
            nn.init.xavier_uniform_(m.weight)
            if m.bias is not None:
                nn.init.zeros_(m.bias)
    model.apply(_init_weights)

    criterion = torch.nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    from torch.optim.lr_scheduler import CosineAnnealingLR
    scheduler = CosineAnnealingLR(optimizer, T_max=args.epochs)

    best_val_loss = float("inf")
    best_epoch = -1
    best_state = None
    patience_left = args.patience

    for epoch in range(args.epochs):
        model.train()
        train_loss_sum = 0.0
        n_batches = 0
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            out = model(bx)
            loss = criterion(out, by)
            loss.backward()
            optimizer.step()
            train_loss_sum += loss.item()
            n_batches += 1
        train_loss = train_loss_sum / max(n_batches, 1)

        model.eval()
        with torch.no_grad():
            val_pred_scaled = model(X_val_t.to(device)).detach().cpu().numpy().ravel()
        val_loss = float(np.mean((val_pred_scaled - y_val_scaled_arr) ** 2))
        scheduler.step()
        print(f"epoch {epoch + 1}/{args.epochs} train_loss={train_loss:.4f} val_loss={val_loss:.4f}", flush=True)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch + 1
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_left = args.patience
        else:
            patience_left -= 1
            if patience_left <= 0:
                print(f"Early stopping at epoch {epoch + 1}, best epoch {best_epoch}", flush=True)
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        val_pred_scaled = model(X_val_t.to(device)).detach().cpu().numpy().ravel()
    val_pred = y_scaler.inverse_transform(val_pred_scaled.reshape(-1, 1)).ravel()
    _print_eval(y_val, val_pred)
    _print_threshold_acc(y_val, val_pred)

    model_out = Path(args.model_out)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), model_out)
    print(f"Model saved: {model_out} (best epoch {best_epoch})", flush=True)
    print(f"训练耗时: {time.time() - t_start:.2f} 秒", flush=True)

    scaler_path = model_out.with_name(model_out.stem + ".scaler.joblib")
    try:
        import joblib
        joblib.dump({"scaler": scaler, "features": features, "y_scaler": y_scaler, "hidden_sizes": hidden_sizes}, scaler_path)
        print(f"Scaler saved: {scaler_path} (含 X scaler + y_scaler + hidden_sizes)")
    except Exception:
        pass

    if getattr(args, "shap", False):
        _run_shap(model, X_val, features, device, args, y_scaler)

    return 0


def _print_eval(y_true: np.ndarray, y_pred: np.ndarray) -> None:
    try:
        from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
        mse = mean_squared_error(y_true, y_pred)
        mae = mean_absolute_error(y_true, y_pred)
        r2 = r2_score(y_true, y_pred)
        dir_acc = (np.sign(y_pred) == np.sign(y_true)).mean()
        print(f"Val MSE: {mse:.4f}, MAE: {mae:.4f}, R2: {r2:.4f}, 方向准确率: {dir_acc:.4f}", flush=True)
    except Exception as e:
        print(f"Eval error: {e}", file=sys.stderr)


def _print_threshold_acc(y_true: np.ndarray, y_pred: np.ndarray) -> None:
    for th in [0.2, 0.5, 1.0, 1.5]:
        mask = np.abs(y_pred) >= th
        n = int(mask.sum())
        if n > 0:
            acc = (np.sign(y_pred[mask]) == np.sign(y_true[mask])).mean()
            print(f"  |zeff|>={th} 方向准确率: {acc:.4f} (样本 {n})", flush=True)
        else:
            print(f"  |zeff|>={th} 样本 0", flush=True)


def _run_shap(model: ZeffMLP, X_val: pd.DataFrame, features: list[str], device: torch.device, args: argparse.Namespace, y_scaler=None) -> None:
    n_shap = min(getattr(args, "shap_n", 2000), len(X_val))
    X_sub = X_val.iloc[:n_shap].values.astype(np.float32)
    try:
        import shap
        model.eval()
        background = torch.tensor(X_sub[:100], dtype=torch.float32).to(device)
        explainer = shap.DeepExplainer(model, background)
        X_explain = torch.tensor(X_sub[:200], dtype=torch.float32).to(device)
        shap_vals = explainer.shap_values(X_explain)
        if isinstance(shap_vals, list):
            shap_vals = shap_vals[0]
        if isinstance(shap_vals, torch.Tensor):
            shap_vals = shap_vals.detach().cpu().numpy()
        print("SHAP summary (feature importance):", flush=True)
        for i, name in enumerate(features):
            print(f"  {name}: mean |SHAP| = {np.abs(shap_vals[:, i]).mean():.4f}", flush=True)
    except Exception as e:
        print(f"SHAP failed: {e}", file=sys.stderr)


def _resolve_device(device_arg: str) -> torch.device:
    if device_arg == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device("cuda" if device_arg in ("cuda", "gpu") else "cpu")


def cmd_predict(args: argparse.Namespace) -> int:
    """Load .pth + optional scaler, predict zeff, write CSV with zeff column."""
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"Error: model not found: {model_path}", file=sys.stderr)
        return 1
    inp = Path(args.input_csv)
    if not inp.exists():
        print(f"Error: input CSV not found: {inp}", file=sys.stderr)
        return 1

    df = pd.read_csv(inp)
    if getattr(args, "high_acc_only", False) and "t_unix" in df.columns:
        df["hour"] = pd.to_datetime(df["t_unix"], unit="s", utc=True).dt.hour
        df = df[df["hour"].isin(HIGH_ACC_HOURS_UTC)].reset_index(drop=True)
        print(f"high-acc-only 过滤后行数: {len(df)}", flush=True)

    scaler_path = Path(args.scaler) if getattr(args, "scaler", None) else model_path.with_name(model_path.stem + ".scaler.joblib")
    if not scaler_path.is_absolute() and not scaler_path.exists():
        scaler_path = model_path.parent / scaler_path.name
    features = list(FEATURES)
    scaler = None
    y_scaler = None
    hidden_sizes = DEFAULT_HIDDEN_SIZES
    if scaler_path.exists():
        try:
            import joblib
            data = joblib.load(scaler_path)
            scaler = data.get("scaler")
            features = data.get("features", features)
            y_scaler = data.get("y_scaler")
            hidden_sizes = data.get("hidden_sizes", DEFAULT_HIDDEN_SIZES)
        except Exception:
            pass

    if "hour_of_day" not in df.columns and "t_unix" in df.columns:
        df["hour_of_day"] = pd.to_datetime(df["t_unix"], unit="s", errors="coerce").dt.hour
    for c in features:
        if c not in df.columns:
            df[c] = 0.0
    X = df[features].fillna(0.0)
    if scaler is not None:
        X = scaler.transform(X)
    X_arr = X.values if hasattr(X, "values") else np.asarray(X, dtype=np.float32)
    X_t = torch.tensor(X_arr, dtype=torch.float32)
    device = _resolve_device(getattr(args, "device", "auto"))
    model = ZeffMLP(input_dim=len(features), hidden_sizes=hidden_sizes, dropout_p=0.3).to(device)
    model.load_state_dict(torch.load(str(model_path), map_location=device))
    model.eval()
    with torch.no_grad():
        zeff_scaled = model(X_t.to(device)).detach().cpu().numpy().ravel()
    if y_scaler is not None:
        zeff = y_scaler.inverse_transform(zeff_scaled.reshape(-1, 1)).ravel()
    else:
        zeff = zeff_scaled
    out_df = df.copy()
    out_df["zeff"] = zeff
    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out_df)} rows)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MLP zeff AI: train / predict delta_odds_rel (zeff).")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("train", help="Train MLP on train_data CSV")
    t.add_argument("--train-csv", type=Path, default=Path(__file__).resolve().parent / "train_data.csv", help="Train CSV")
    t.add_argument("--model-out", type=Path, default=Path(__file__).resolve().parent / "zeff_mlp_model.pth", help="Output .pth path")
    t.add_argument("--train-ratio", type=float, default=0.8, help="Fraction of rows for train (time-ordered)")
    t.add_argument("--scale", action="store_true", help="StandardScaler on features")
    t.add_argument("--high-acc-only", action="store_true", help="只用 high acc 时段训练")
    t.add_argument("--clip-zscore-min", type=float, default=-5.0, help="zscore clip 下界")
    t.add_argument("--clip-zscore-max", type=float, default=5.0, help="zscore clip 上界")
    t.add_argument("--clip-target-min", type=float, default=-10.0, help="delta_odds_rel clip 下界")
    t.add_argument("--clip-target-max", type=float, default=10.0, help="delta_odds_rel clip 上界")
    t.add_argument("--hidden-sizes", type=str, default="1024 512 256 128", help="隐藏层宽度，空格分隔，如 '1024 512 256 128'")
    t.add_argument("--epochs", type=int, default=50, help="Max epochs (配合早停)")
    t.add_argument("--batch-size", type=int, default=8192, help="Batch size (5070 显存支持 8192)")
    t.add_argument("--lr", type=float, default=0.02, help="Learning rate")
    t.add_argument("--dropout-p", type=float, default=0.3, help="Dropout probability")
    t.add_argument("--weight-decay", type=float, default=5e-4, help="L2 weight decay (Adam)，建议 5e-4（178k 参数）或 1e-3（强正则）")
    t.add_argument("--patience", type=int, default=5, help="Early stopping patience (epochs)")
    t.add_argument("--device", type=str, default="auto", choices=["auto", "cuda", "cpu", "gpu"], help="Device")
    t.add_argument("--shap", action="store_true", help="Run SHAP after training (validation subset)")
    t.add_argument("--shap-n", type=int, default=2000, help="Max samples for SHAP")
    t.set_defaults(func=cmd_train)

    p = sub.add_parser("predict", help="Predict zeff from CSV")
    p.add_argument("--model", type=Path, default=Path(__file__).resolve().parent / "zeff_mlp_model.pth", help="Model .pth path")
    p.add_argument("--input-csv", type=Path, required=True, help="Input CSV with feature columns")
    p.add_argument("--output-csv", type=Path, default=Path(__file__).resolve().parent / "zeff_predicted.csv", help="Output CSV with zeff column")
    p.add_argument("--scaler", type=Path, default=None, help="Scaler joblib (default: model stem .scaler.joblib)")
    p.add_argument("--device", type=str, default="auto", choices=["auto", "cuda", "cpu", "gpu"])
    p.add_argument("--high-acc-only", action="store_true", help="只预测 high acc 时段")
    p.set_defaults(func=cmd_predict)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
