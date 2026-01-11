#!/usr/bin/env python3
"""
测试Polymarket多市场API - ETH 15分钟 + BTC/ETH 1小时市场

测试市场：
1. eth-updown-15m-{timestamp}
2. bitcoin-up-or-down-january-10-9pm-et
3. ethereum-up-or-down-january-10-9pm-et
"""

import requests
import json
from datetime import datetime, timezone

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

def test_market_by_slug(slug: str):
    """测试通过slug查询市场"""
    print(f"\n{'='*60}")
    print(f"测试市场: {slug}")
    print('='*60)
    
    try:
        # 1. 查询市场
        url = f"{GAMMA_API}/markets"
        params = {"slug": slug}
        
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        
        markets = resp.json()
        
        if not markets:
            print(f"❌ 市场不存在: {slug}")
            return None
        
        market = markets[0]
        print(f"✓ 找到市场")
        print(f"  标题: {market.get('question', 'N/A')}")
        print(f"  Condition ID: {market.get('condition_id', 'N/A')}")
        
        # 2. 获取token信息
        tokens = market.get('tokens', [])
        if len(tokens) >= 2:
            print(f"  Token数量: {len(tokens)}")
            for i, token in enumerate(tokens[:2]):
                print(f"    Token {i}: {token.get('outcome', 'N/A')} - {token.get('token_id', 'N/A')}")
            
            # 3. 测试获取orderbook
            token_id = tokens[0].get('token_id')
            if token_id:
                print(f"\n  测试获取orderbook...")
                book_url = f"{CLOB_API}/book"
                book_resp = requests.get(book_url, params={"token_id": token_id}, timeout=10)
                
                if book_resp.status_code == 200:
                    book = book_resp.json()
                    bids = book.get('bids', [])
                    asks = book.get('asks', [])
                    print(f"  ✓ Orderbook可访问")
                    print(f"    Bids: {len(bids)} levels")
                    print(f"    Asks: {len(asks)} levels")
                    if bids and asks:
                        print(f"    Best Bid: {bids[0].get('price', 'N/A')}")
                        print(f"    Best Ask: {asks[0].get('price', 'N/A')}")
                else:
                    print(f"  ⚠ Orderbook返回状态: {book_resp.status_code}")
        
        return market
        
    except requests.exceptions.RequestException as e:
        print(f"❌ API请求失败: {e}")
        return None
    except Exception as e:
        print(f"❌ 错误: {e}")
        return None


def test_15min_market(asset: str):
    """测试15分钟市场（需要计算当前窗口时间戳）"""
    print(f"\n{'='*60}")
    print(f"测试 {asset.upper()} 15分钟市场")
    print('='*60)
    
    # 计算当前15分钟窗口开始时间
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    window_start = (epoch // 900) * 900  # 向下取整到15分钟
    
    slug = f"{asset}-updown-15m-{window_start}"
    print(f"当前窗口slug: {slug}")
    
    return test_market_by_slug(slug)


def test_eth_cex_orderbook():
    """测试ETH的CEX orderbook"""
    print(f"\n{'='*60}")
    print("测试 ETH CEX Orderbook")
    print('='*60)
    
    venues = {
        'binance_spot': 'https://api.binance.com/api/v3/depth?symbol=ETHUSDT&limit=10',
        'okx_spot': 'https://www.okx.com/api/v5/market/books?instId=ETH-USDT',
        'bybit_spot': 'https://api.bybit.com/v5/market/orderbook?category=spot&symbol=ETHUSDT'
    }
    
    results = {}
    
    for venue, url in venues.items():
        try:
            print(f"\n{venue}:")
            resp = requests.get(url, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                print(f"  ✓ API可访问")
                
                # 解析不同交易所的格式
                if venue == 'binance_spot':
                    bids = data.get('bids', [])
                    asks = data.get('asks', [])
                    if bids and asks:
                        print(f"    Best Bid: {bids[0][0]}")
                        print(f"    Best Ask: {asks[0][0]}")
                        results[venue] = True
                
                elif venue == 'okx_spot':
                    books = data.get('data', [])
                    if books:
                        bids = books[0].get('bids', [])
                        asks = books[0].get('asks', [])
                        if bids and asks:
                            print(f"    Best Bid: {bids[0][0]}")
                            print(f"    Best Ask: {asks[0][0]}")
                            results[venue] = True
                
                elif venue == 'bybit_spot':
                    result = data.get('result', {})
                    bids = result.get('b', [])
                    asks = result.get('a', [])
                    if bids and asks:
                        print(f"    Best Bid: {bids[0][0]}")
                        print(f"    Best Ask: {asks[0][0]}")
                        results[venue] = True
            else:
                print(f"  ✗ 状态码: {resp.status_code}")
                results[venue] = False
                
        except Exception as e:
            print(f"  ✗ 错误: {e}")
            results[venue] = False
    
    return results


def main():
    print("Polymarket多市场API测试")
    print("=" * 60)
    
    # 测试1: ETH 15分钟市场
    print("\n【测试1】ETH 15分钟市场")
    eth_15m = test_15min_market("eth")
    
    # 测试2: BTC 15分钟市场（验证）
    print("\n【测试2】BTC 15分钟市场（验证）")
    btc_15m = test_15min_market("btc")
    
    # 测试3: BTC 1小时市场
    print("\n【测试3】BTC 1小时市场")
    # 注意：1小时市场的slug会随日期变化，这里测试一个示例
    btc_1h_slug = "bitcoin-up-or-down-january-10-9pm-et"
    btc_1h = test_market_by_slug(btc_1h_slug)
    
    # 测试4: ETH 1小时市场  
    print("\n【测试4】ETH 1小时市场")
    eth_1h_slug = "ethereum-up-or-down-january-10-9pm-et"
    eth_1h = test_market_by_slug(eth_1h_slug)
    
    # 测试5: ETH CEX orderbook
    print("\n【测试5】ETH CEX Orderbook")
    eth_cex = test_eth_cex_orderbook()
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    results = {
        "ETH 15分钟市场": eth_15m is not None,
        "BTC 15分钟市场": btc_15m is not None,
        "BTC 1小时市场": btc_1h is not None,
        "ETH 1小时市场": eth_1h is not None,
        "ETH CEX Orderbook": all(eth_cex.values()) if eth_cex else False
    }
    
    for name, success in results.items():
        icon = "✓" if success else "✗"
        print(f"{icon} {name}")
    
    all_success = all(results.values())
    
    if all_success:
        print("\n🎉 所有市场测试通过！可以开始编写采集脚本")
    else:
        print("\n⚠️  部分市场测试失败，需要检查")
    
    return 0 if all_success else 1


if __name__ == "__main__":
    exit(main())

