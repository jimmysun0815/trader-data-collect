#!/usr/bin/env python3
"""
Polymarket多市场实时数据采集器

支持市场：
- BTC 15分钟市场: btc-updown-15m-{timestamp}
- ETH 15分钟市场: eth-updown-15m-{timestamp}
- BTC 1小时市场: bitcoin-up-or-down-{date}-{hour}pm-et
- ETH 1小时市场: ethereum-up-or-down-{date}-{hour}pm-et

输出格式：每15分钟一个JSONL文件
输出路径：../real_hot/{market_slug}_{timestamp}.jsonl
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# API endpoints
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# 输出目录
OUTPUT_DIR = Path(__file__).parent / "real_hot"

# 市场配置
MARKETS = {
    "btc_15m": {
        "type": "15min",
        "asset": "btc",
        "slug_prefix": "btc-updown-15m"
    },
    "eth_15m": {
        "type": "15min",
        "asset": "eth",
        "slug_prefix": "eth-updown-15m"
    },
    "btc_1h": {
        "type": "1hour",
        "asset": "btc",
        "slug_prefix": "bitcoin-up-or-down"
    },
    "eth_1h": {
        "type": "1hour",
        "asset": "eth",
        "slug_prefix": "ethereum-up-or-down"
    }
}


def get_15min_window_slug(asset: str, epoch: Optional[int] = None) -> str:
    """计算15分钟窗口的slug"""
    if epoch is None:
        epoch = int(time.time())
    window_start = (epoch // 900) * 900
    return f"{asset}-updown-15m-{window_start}"


def get_1hour_market_slug(asset: str, target_time: Optional[datetime] = None) -> str:
    """
    计算1小时市场的slug
    
    格式: {asset}-up-or-down-{month}-{day}-{hour}pm-et
    例如: bitcoin-up-or-down-january-10-9pm-et
    """
    if target_time is None:
        # 使用UTC时间转换为ET时间（UTC-5）
        target_time = datetime.now(timezone.utc) - timedelta(hours=5)
    
    # 向下取整到当前小时
    target_time = target_time.replace(minute=0, second=0, microsecond=0)
    
    month = target_time.strftime('%B').lower()  # january
    day = target_time.day
    hour = target_time.hour
    
    # 转换为12小时制
    ampm = 'am' if hour < 12 else 'pm'
    hour_12 = hour % 12
    if hour_12 == 0:
        hour_12 = 12
    
    asset_name = "bitcoin" if asset == "btc" else "ethereum"
    slug = f"{asset_name}-up-or-down-{month}-{day}-{hour_12}{ampm}-et"
    
    return slug


def fetch_market_info(slug: str) -> Optional[Dict]:
    """通过slug获取市场信息"""
    # 方法1: 使用 /markets/slug/{slug} (推荐)
    try:
        url = f"{GAMMA_API}/markets/slug/{slug}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # Gamma有时会包一层 {"market": {...}}
        if isinstance(data, dict) and isinstance(data.get("market"), dict):
            return data["market"]
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[WARN] Failed to fetch market by slug endpoint {slug}: {e}", file=sys.stderr)
    
    # 方法2: 兜底，使用search
    try:
        url = f"{GAMMA_API}/markets"
        resp = requests.get(url, params={"limit": 50, "search": slug}, timeout=10)
        resp.raise_for_status()
        markets = resp.json()
        
        if isinstance(markets, list):
            for m in markets:
                if isinstance(m, dict) and m.get("slug") == slug:
                    return m
    except Exception as e:
        print(f"[ERROR] Failed to fetch market {slug}: {e}", file=sys.stderr)
    
    return None


def fetch_orderbook(token_id: str) -> Optional[Dict]:
    """获取orderbook数据"""
    try:
        url = f"{CLOB_API}/book"
        resp = requests.get(url, params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Failed to fetch orderbook for {token_id}: {e}", file=sys.stderr)
        return None


def collect_market_tick(market_info: Dict, market_key: str) -> Optional[Dict]:
    """
    采集单个市场的tick数据
    
    返回格式：
    {
        "timestamp": epoch_ms,
        "market_key": "btc_15m",
        "market_slug": "btc-updown-15m-xxx",
        "tokens": [
            {
                "outcome": "Yes",
                "token_id": "xxx",
                "orderbook": {...}
            },
            ...
        ]
    }
    """
    try:
        tick = {
            "timestamp": int(time.time() * 1000),
            "market_key": market_key,
            "market_slug": market_info.get("slug"),
            "question": market_info.get("question"),
            "tokens": []
        }
        
        # 获取每个token的orderbook
        tokens = market_info.get("tokens", [])
        if not tokens and market_info:
            # 兜底：尝试从其他字段提取token信息
            pass
        
        for token in tokens[:2]:  # 通常只有Yes/No两个
            # 尝试多种可能的token_id字段
            token_id = (token.get("token_id") or 
                       token.get("clobTokenId") or 
                       token.get("clob_token_id") or 
                       token.get("tokenId"))
            
            if not token_id:
                continue
            
            orderbook = fetch_orderbook(str(token_id))
            if orderbook:
                # 提取bids和asks
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                
                # 转换格式：[{"price": "0.5", "size": "100"}] -> [[0.5, 100]]
                bids_formatted = []
                asks_formatted = []
                
                for bid in bids:
                    try:
                        if isinstance(bid, dict):
                            p = float(bid.get("price", 0))
                            s = float(bid.get("size", 0))
                            bids_formatted.append([p, s])
                        elif isinstance(bid, list) and len(bid) >= 2:
                            bids_formatted.append([float(bid[0]), float(bid[1])])
                    except:
                        continue
                
                for ask in asks:
                    try:
                        if isinstance(ask, dict):
                            p = float(ask.get("price", 0))
                            s = float(ask.get("size", 0))
                            asks_formatted.append([p, s])
                        elif isinstance(ask, list) and len(ask) >= 2:
                            asks_formatted.append([float(ask[0]), float(ask[1])])
                    except:
                        continue
                
                tick["tokens"].append({
                    "outcome": token.get("outcome") or token.get("name"),
                    "token_id": str(token_id),
                    "orderbook": {
                        "bids": bids_formatted,
                        "asks": asks_formatted
                    }
                })
        
        return tick if tick["tokens"] else None
        
    except Exception as e:
        print(f"[ERROR] Failed to collect tick for {market_key}: {e}", file=sys.stderr)
        return None


def get_output_file(market_slug: str) -> Path:
    """获取输出文件路径"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{market_slug}_{timestamp}.jsonl"
    return OUTPUT_DIR / filename


def main():
    """主循环：每秒采集一次所有市场"""
    print(f"[INFO] Polymarket多市场采集器启动")
    print(f"[INFO] 输出目录: {OUTPUT_DIR}")
    print(f"[INFO] 采集市场: {', '.join(MARKETS.keys())}")
    
    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 当前市场状态
    current_markets = {}  # {market_key: {"info": ..., "file": ...}}
    
    while True:
        try:
            now = int(time.time())
            
            # 检查并更新每个市场
            for market_key, config in MARKETS.items():
                market_type = config["type"]
                asset = config["asset"]
                
                # 计算当前窗口的slug
                if market_type == "15min":
                    slug = get_15min_window_slug(asset, now)
                elif market_type == "1hour":
                    slug = get_1hour_market_slug(asset)
                else:
                    continue
                
                # 检查是否需要更新市场信息
                need_update = False
                if market_key not in current_markets:
                    need_update = True
                elif current_markets[market_key]["info"]["slug"] != slug:
                    # 窗口切换，关闭旧文件
                    old_file = current_markets[market_key].get("file")
                    if old_file:
                        old_file.close()
                    need_update = True
                
                if need_update:
                    print(f"[INFO] Fetching new market: {market_key} -> {slug}")
                    market_info = fetch_market_info(slug)
                    
                    if market_info:
                        output_file = get_output_file(slug)
                        file_handle = open(output_file, "a")
                        
                        current_markets[market_key] = {
                            "info": market_info,
                            "slug": slug,
                            "file": file_handle,
                            "file_path": output_file
                        }
                        
                        print(f"[INFO] {market_key}: {market_info.get('question')}")
                        print(f"[INFO] Output: {output_file}")
                    else:
                        print(f"[WARN] Market not found: {slug}", file=sys.stderr)
                        # 保留旧的市场信息继续采集
                        continue
                
                # 采集tick数据
                if market_key in current_markets:
                    market_state = current_markets[market_key]
                    tick = collect_market_tick(market_state["info"], market_key)
                    
                    if tick:
                        # 写入JSONL
                        file_handle = market_state["file"]
                        file_handle.write(json.dumps(tick) + "\n")
                        file_handle.flush()
                        
                        # 输出状态
                        if len(tick["tokens"]) >= 1:
                            token_book = tick["tokens"][0]["orderbook"]
                            bids = token_book.get("bids", [])
                            asks = token_book.get("asks", [])
                            best_bid = bids[0][0] if bids and len(bids[0]) > 0 else None
                            best_ask = asks[0][0] if asks and len(asks[0]) > 0 else None
                            print(f"[{market_key}] bid={best_bid if best_bid else 'N/A'} "
                                  f"ask={best_ask if best_ask else 'N/A'}", flush=True)
            
            # 每秒采集一次
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down...")
            break
        except Exception as e:
            print(f"[ERROR] Main loop error: {e}", file=sys.stderr)
            time.sleep(5)  # 出错后等待5秒
    
    # 关闭所有文件
    for market_key, state in current_markets.items():
        if "file" in state and state["file"]:
            state["file"].close()
            print(f"[INFO] Closed {market_key}: {state['file_path']}")
    
    print("[INFO] Recorder stopped")


if __name__ == "__main__":
    main()

