"""
╔══════════════════════════════════════════════════════╗
║       CRYPTO SCALPING SIGNAL BOT v2.2 Railway       ║
║       TF: 5M / 15M | Exchange: Bybit Perp           ║
╚══════════════════════════════════════════════════════╝
Deploy ke Railway — semua config via Environment Variables.
"""

import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import time
from datetime import datetime, timezone
import math
from aiohttp import web

# ════════════════════════════════════════════════════════
#   CONFIG — Baca dari Environment Variables Railway
# ════════════════════════════════════════════════════════

DISCORD_TOKEN   = os.environ["DISCORD_TOKEN"]
SIGNAL_CHANNEL  = int(os.environ["SIGNAL_CHANNEL_ID"])
ALERT_CHANNEL   = int(os.environ.get("ALERT_CHANNEL_ID", os.environ["SIGNAL_CHANNEL_ID"]))
PREFIX          = os.environ.get("PREFIX", "!")
SCAN_INTERVAL   = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))
ROLE_MENTION    = os.environ.get("SIGNAL_ROLE_MENTION", "")
PORT            = int(os.environ.get("PORT", "8080"))   # Railway inject PORT otomatis

# ─── Bybit API ────────────────────────────────────────
BYBIT_BASE      = "https://api.bybit.com"
KLINE_EP        = "/v5/market/kline"
TICKER_EP       = "/v5/market/tickers"

# ─── Watchlist Bybit Perp ─────────────────────────────
WATCHLIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT",
    "MATICUSDT","LTCUSDT","UNIUSDT","ATOMUSDT","NEARUSDT",
    "APTUSDT","ARBUSDT","OPUSDT","SUIUSDT","SEIUSDT",
    "INJUSDT","TIAUSDT","FETUSDT","RNDRUSDT","WLDUSDT",
    "ORDIUSDT","JUPUSDT","EIGENUSDT","NOTUSDT","1000PEPEUSDT",
]

# ─── Bot Setup ────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
# help_command=None wajib agar tidak bentrok dengan !help custom kita
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ─── State ────────────────────────────────────────────
active_signals  = {}
signal_history  = []
bot_start_time  = None
scan_count      = 0
signal_count    = 0

# ════════════════════════════════════════════════════════
#   KEEP-ALIVE WEB SERVER (wajib untuk Railway)
# ════════════════════════════════════════════════════════

async def health_handler(request):
    uptime = int(time.time() - bot_start_time) if bot_start_time else 0
    return web.Response(
        text=(
            f"OK - CryptoScalp Pro ONLINE\n"
            f"Uptime : {uptime//3600}h {(uptime%3600)//60}m\n"
            f"Scans  : {scan_count}\n"
            f"Signals: {signal_count}\n"
            f"Pairs  : {len(WATCHLIST)}"
        ),
        content_type="text/plain"
    )

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"  Web server aktif di port {PORT}")

# ════════════════════════════════════════════════════════
#   BYBIT API HELPERS
# ════════════════════════════════════════════════════════

async def fetch_klines(session, symbol, interval, limit=150):
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with session.get(BYBIT_BASE + KLINE_EP, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            if data.get("retCode") != 0:
                print(f"  ⚠️ fetch_klines [{symbol}] retCode={data.get('retCode')} msg={data.get('retMsg')}")
                return []
            items = data.get("result", {}).get("list", [])
            return [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                     "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                    for c in reversed(items)]
    except Exception as e:
        print(f"  ⚠️ fetch_klines [{symbol}] exception: {e}")
    return []

async def fetch_ticker(session, symbol):
    params = {"category": "linear", "symbol": symbol}
    try:
        async with session.get(BYBIT_BASE + TICKER_EP, params=params,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json()
            if data.get("retCode") != 0:
                print(f"  ⚠️ fetch_ticker [{symbol}] retCode={data.get('retCode')} msg={data.get('retMsg')}")
                return None
            items = data.get("result", {}).get("list", [])
            if not items:
                print(f"  ⚠️ fetch_ticker [{symbol}] list kosong")
                return None
            t = items[0]
            return {
                "last":    float(t.get("lastPrice",  t.get("markPrice", 0))),
                "mark":    float(t.get("markPrice",  0)),
                "index":   float(t.get("indexPrice", t.get("markPrice", 0))),
                "funding": float(t.get("fundingRate", 0)) * 100,
                "vol24h":  float(t.get("volume24h",  0)),
                "pct24h":  float(t.get("price24hPcnt", 0)) * 100,
                "high24h": float(t.get("highPrice24h", 0)),
                "low24h":  float(t.get("lowPrice24h",  0)),
            }
    except Exception as e:
        print(f"  ⚠️ fetch_ticker [{symbol}] exception: {e}")
    return None

async def fetch_all_tickers(session):
    try:
        async with session.get(BYBIT_BASE + TICKER_EP, params={"category": "linear"},
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json()
            if data.get("retCode") != 0:
                print(f"  ⚠️ fetch_all_tickers retCode={data.get('retCode')} msg={data.get('retMsg')}")
                return {}
            return {t["symbol"]: t for t in data.get("result", {}).get("list", [])
                    if t["symbol"].endswith("USDT")}
    except Exception as e:
        print(f"  ⚠️ fetch_all_tickers exception: {e}")
    return {}

# ════════════════════════════════════════════════════════
#   TECHNICAL ANALYSIS ENGINE
# ════════════════════════════════════════════════════════

def calc_ema(closes, period):
    if len(closes) < period:
        return []
    ema = [sum(closes[:period]) / period]
    k = 2 / (period + 1)
    for p in closes[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    return round(100.0 if al == 0 else 100 - 100/(1 + ag/al), 2)

def calc_macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow + sig:
        return None, None, None
    ef = calc_ema(closes, fast)
    es = calc_ema(closes, slow)
    n = min(len(ef), len(es))
    ml = [ef[-(n-i)] - es[-(n-i)] for i in range(n)]
    sl = calc_ema(ml, sig)
    hist = [ml[-(len(sl)-i)] - sl[-(len(sl)-i)] for i in range(len(sl))]
    return round(ml[-1], 6), round(sl[-1], 6), round(hist[-1], 6)

def calc_bollinger(closes, period=20, sd=2):
    if len(closes) < period:
        return None, None, None
    r = closes[-period:]
    m = sum(r)/period
    s = math.sqrt(sum((x-m)**2 for x in r)/period)
    return round(m+sd*s,6), round(m,6), round(m-sd*s,6)

def calc_atr(candles, period=14):
    if len(candles) < period+1:
        return 0
    trs = [max(c["high"]-c["low"], abs(c["high"]-candles[i-1]["close"]),
               abs(c["low"]-candles[i-1]["close"]))
           for i, c in enumerate(candles) if i > 0]
    atr = sum(trs[:period])/period
    for tr in trs[period:]:
        atr = (atr*(period-1)+tr)/period
    return round(atr, 8)

def calc_stoch_rsi(closes, rp=14, sp=14, sk=3, sd=3):
    if len(closes) < rp+sp+sk+sd:
        return 50.0, 50.0
    rv = [calc_rsi(closes[:i], rp) for i in range(rp, len(closes)+1)]
    sv = []
    for i in range(sp, len(rv)+1):
        w = rv[i-sp:i]; lo, hi = min(w), max(w)
        sv.append(0.0 if hi==lo else (rv[i-1]-lo)/(hi-lo)*100)
    k = [sum(sv[i:i+sk])/sk for i in range(len(sv)-sk+1)]
    d = [sum(k[i:i+sd])/sd for i in range(len(k)-sd+1)]
    return (round(k[-1],2) if k else 50.0), (round(d[-1],2) if d else 50.0)

def detect_divergence(closes, rsi_vals, lb=10):
    if len(closes) < lb or len(rsi_vals) < lb:
        return None
    pt = closes[-1]-closes[-lb]; rt = rsi_vals[-1]-rsi_vals[-lb]
    if pt < 0 and rt > 0: return "BULLISH_DIV"
    if pt > 0 and rt < 0: return "BEARISH_DIV"
    return None

def calc_vol_spike(candles, period=20):
    if len(candles) < period+1:
        return 1.0
    avg = sum(c["volume"] for c in candles[-period-1:-1])/period
    return round(candles[-1]["volume"]/avg, 2) if avg else 1.0

def find_sr(candles, lb=50):
    if len(candles) < lb:
        return None, None
    return (round(min(c["low"] for c in candles[-lb:]),8),
            round(max(c["high"] for c in candles[-lb:]),8))

# ════════════════════════════════════════════════════════
#   SIGNAL ENGINE
# ════════════════════════════════════════════════════════

def score_signal(ind):
    sl, ss, reasons = 0, 0, []
    rsi=ind["rsi"]; macd=ind["macd"]; ms=ind["macd_sig"]; mh=ind["macd_hist"]
    bbu=ind["bb_upper"]; bbm=ind["bb_mid"]; bbl=ind["bb_lower"]
    close=ind["close"]; e9=ind["ema9"]; e21=ind["ema21"]; e50=ind["ema50"]
    stk=ind["stoch_k"]; std_=ind["stoch_d"]; vs=ind["vol_spike"]
    div=ind["divergence"]; sup=ind["support"]; res=ind["resistance"]

    # RSI
    if rsi < 30:   sl+=20; reasons.append(f"📉 RSI oversold ({rsi})")
    elif rsi < 40: sl+=10; reasons.append(f"📉 RSI near oversold ({rsi})")
    elif rsi > 70: ss+=20; reasons.append(f"📈 RSI overbought ({rsi})")
    elif rsi > 60: ss+=10; reasons.append(f"📈 RSI near overbought ({rsi})")

    # MACD
    if macd and ms:
        if macd > ms and mh and mh > 0:   sl+=18; reasons.append("✅ MACD bullish crossover")
        elif macd < ms and mh and mh < 0: ss+=18; reasons.append("✅ MACD bearish crossover")
        if mh and mh > 0: sl+=5
        elif mh and mh < 0: ss+=5

    # EMA
    if e9 and e21 and e50:
        if close > e9 > e21 > e50:    sl+=15; reasons.append("📊 EMA 9/21/50 uptrend")
        elif close < e9 < e21 < e50:  ss+=15; reasons.append("📊 EMA 9/21/50 downtrend")
        elif close > e9 > e21: sl+=8
        elif close < e9 < e21: ss+=8

    # Bollinger
    if bbl and bbu and bbm:
        bw = (bbu-bbl)/bbm
        if close <= bbl:   sl+=15; reasons.append("🎯 BB Lower bounce zone")
        elif close >= bbu: ss+=15; reasons.append("🎯 BB Upper reversal zone")
        if bw < 0.015:     sl+=5; ss+=5; reasons.append("⚡ BB Squeeze — breakout incoming")

    # StochRSI
    if stk < 20 and std_ < 20:   sl+=12; reasons.append(f"🔵 StochRSI oversold K:{stk} D:{std_}")
    elif stk > 80 and std_ > 80: ss+=12; reasons.append(f"🔴 StochRSI overbought K:{stk} D:{std_}")
    if stk > std_ and stk < 50: sl+=6
    elif stk < std_ and stk > 50: ss+=6

    # FIX BUG #1: Volume hanya boost arah yang sudah dominan, bukan keduanya
    if vs >= 2.0:
        reasons.append(f"🔥 Volume spike {vs}x")
        if sl > ss: sl += 8
        else: ss += 8
    elif vs >= 1.5:
        reasons.append(f"📢 Volume elevated {vs}x")
        if sl > ss: sl += 4
        else: ss += 4

    # Divergence
    if div == "BULLISH_DIV": sl+=15; reasons.append("💎 Bullish RSI Divergence")
    elif div == "BEARISH_DIV": ss+=15; reasons.append("💎 Bearish RSI Divergence")

    # S/R
    if sup and res:
        rng = res-sup
        if rng > 0:
            pos = (close-sup)/rng
            if pos < 0.15:   sl+=10; reasons.append(f"📌 Near Support ({sup:.4f})")
            elif pos > 0.85: ss+=10; reasons.append(f"📌 Near Resistance ({res:.4f})")

    total = sl+ss
    if total == 0: return 0, "NEUTRAL", reasons

    # FIX BUG #2: Threshold diturunkan dari 45→30 dan rasio dari 1.3→1.2
    # agar sinyal bisa keluar di kondisi market normal
    if sl >= 30 and sl > ss*1.2:
        return min(round(sl/total*100), 95), "LONG", reasons
    if ss >= 30 and ss > sl*1.2:
        return min(round(ss/total*100), 95), "SHORT", reasons
    return 0, "NEUTRAL", reasons

def calc_targets(direction, entry, atr, _):
    m = [1.2, 2.0, 3.5, 5.5]
    if direction == "LONG":
        sl = round(entry-atr*m[0], 6)
        tp1,tp2,tp3 = round(entry+atr*m[1],6), round(entry+atr*m[2],6), round(entry+atr*m[3],6)
    else:
        sl = round(entry+atr*m[0], 6)
        tp1,tp2,tp3 = round(entry-atr*m[1],6), round(entry-atr*m[2],6), round(entry-atr*m[3],6)
    risk = abs(entry-sl)
    rr = round(abs(tp1-entry)/risk, 2) if risk else 0
    return sl, tp1, tp2, tp3, rr

def suggest_lev(conf, atr_pct):
    mx = 5 if atr_pct > 2 else 10 if atr_pct > 1 else 20
    if conf >= 80: return min(mx, 15)
    if conf >= 65: return min(mx, 10)
    return min(mx, 5)

# ════════════════════════════════════════════════════════
#   EMBED BUILDER
# ════════════════════════════════════════════════════════

def build_embed(sym, tf, direction, entry, sl, tp1, tp2, tp3, rr,
                conf, lev, ind, reasons, ticker):
    color = 0x00FF88 if direction == "LONG" else 0xFF3366
    pct = ticker.get("pct24h", 0)
    ec = "🔥" if conf >= 80 else "⚡" if conf >= 65 else "💡"
    ep = "📈" if pct >= 0 else "📉"
    e = discord.Embed(
        title=f"{'🚀' if direction=='LONG' else '🔽'} SCALPING SIGNAL | {sym}",
        description=(f"**{'🟢 LONG' if direction=='LONG' else '🔴 SHORT'}** | "
                     f"TF: `{tf}M` | {ec} Confidence: `{conf}%`\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"),
        color=color, timestamp=datetime.now(timezone.utc))
    e.set_author(name="⚡ CryptoScalp Pro | Bybit Signal Engine")
    e.add_field(name="📍 Entry",     value=f"`{entry}`",           inline=True)
    e.add_field(name="🛑 Stop Loss", value=f"`{sl}`",              inline=True)
    e.add_field(name="⚖️ R:R",      value=f"`1:{rr}`",            inline=True)
    e.add_field(name="🎯 Take Profit",
                value=f"TP1: `{tp1}`\nTP2: `{tp2}`\nTP3: `{tp3}`", inline=True)
    e.add_field(name="⚙️ Leverage",  value=f"Max `{lev}x` ISOLATE", inline=True)
    e.add_field(name="📊 Market",
                value=(f"Mark: `{ticker.get('mark', entry):.4f}`\n"
                       f"24h: {ep} `{pct:+.2f}%`\n"
                       f"Funding: `{ticker.get('funding', 0):+.4f}%`"), inline=True)
    e.add_field(name="📈 Indicators",
                value=(f"RSI `{ind.get('rsi',0)}` | "
                       f"StochRSI K`{ind.get('stoch_k',0)}` D`{ind.get('stoch_d',0)}`\n"
                       f"MACD Hist `{(ind.get('macd_hist') or 0):+.6f}`\n"
                       f"Vol Spike `{ind.get('vol_spike',1)}x`"), inline=False)
    if reasons:
        e.add_field(name="🧠 Analisa", value="\n".join(reasons[:5]), inline=False)
    e.add_field(name="⚠️ Risk Management",
                value=("• Max **2-3% modal** per trade\n"
                       "• Pasang SL sebelum open posisi\n"
                       "• Partial profit di TP1 (50%)"), inline=False)
    e.set_footer(text="CryptoScalp Pro • Bybit Perp • DYOR • Not Financial Advice")
    return e

# ════════════════════════════════════════════════════════
#   SCANNER
# ════════════════════════════════════════════════════════

async def analyze(session, symbol, tf_min):
    tf_str = {5: "5", 15: "15", 60: "60"}.get(tf_min, "5")
    candles = await fetch_klines(session, symbol, tf_str)
    if len(candles) < 60:
        return None
    closes = [c["close"] for c in candles]
    rsi = calc_rsi(closes)
    macd, ms, mh = calc_macd(closes)
    e9  = (calc_ema(closes, 9)  or [None])[-1]
    e21 = (calc_ema(closes, 21) or [None])[-1]
    e50 = (calc_ema(closes, 50) or [None])[-1]
    bbu, bbm, bbl = calc_bollinger(closes)
    atr = calc_atr(candles)
    stk, std_ = calc_stoch_rsi(closes)
    vs = calc_vol_spike(candles)
    rv = [calc_rsi(closes[:i]) for i in range(14, len(closes)+1)]
    div = detect_divergence(closes, rv)
    sup, res = find_sr(candles)
    ind = dict(close=closes[-1], rsi=rsi, macd=macd, macd_sig=ms, macd_hist=mh,
               ema9=e9, ema21=e21, ema50=e50, bb_upper=bbu, bb_mid=bbm, bb_lower=bbl,
               atr=atr, stoch_k=stk, stoch_d=std_, vol_spike=vs,
               divergence=div, support=sup, resistance=res)
    conf, direction, reasons = score_signal(ind)
    return conf, direction, reasons, ind, atr

async def run_scanner():
    global scan_count, signal_count
    scan_count += 1
    channel = bot.get_channel(SIGNAL_CHANNEL)
    if not channel:
        print("⚠️ Channel tidak ditemukan!")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 Scan #{scan_count}...")
    signals_sent = 0
    movers = []

    async with aiohttp.ClientSession() as session:
        all_t = await fetch_all_tickers(session)
        for sym, t in all_t.items():
            try: movers.append((sym, float(t.get("price24hPcnt", 0))*100))
            except Exception: pass

        for symbol in WATCHLIST:
            try:
                ticker = await fetch_ticker(session, symbol)
                if not ticker: continue

                for tf in [5, 15]:
                    result = await analyze(session, symbol, tf)
                    if not result: continue
                    conf, direction, reasons, ind, atr = result

                    # FIX BUG #3: Turunkan min confidence dari 60 → 55
                    if conf < 55 or direction == "NEUTRAL": continue

                    key = f"{symbol}_{direction}_{tf}"
                    if time.time() - active_signals.get(key, 0) < 1800: continue
                    active_signals[key] = time.time()

                    entry = ticker["mark"]
                    atr_pct = (atr/entry*100) if entry else 1.0
                    sl, tp1, tp2, tp3, rr = calc_targets(direction, entry, atr, ticker)
                    lev = suggest_lev(conf, atr_pct)

                    emb = build_embed(symbol, tf, direction, entry, sl, tp1, tp2, tp3,
                                      rr, conf, lev, ind, reasons, ticker)
                    content = (f"{ROLE_MENTION} " if ROLE_MENTION else "") + \
                              f"🔔 **Signal {direction} | {symbol} TF{tf}M**"
                    await channel.send(content=content, embed=emb)

                    signals_sent += 1
                    signal_count += 1
                    signal_history.insert(0, dict(symbol=symbol, direction=direction,
                                                  tf=tf, entry=entry, confidence=conf,
                                                  time=datetime.now().strftime("%H:%M:%S")))
                    if len(signal_history) > 50: signal_history.pop()
                    print(f"  ✅ {symbol} {direction} TF{tf}M Conf:{conf}%")
                    await asyncio.sleep(1)

            except Exception as ex:
                print(f"  ⚠️ {symbol}: {ex}")
                await asyncio.sleep(0.3)

    if signals_sent == 0 and scan_count % 3 == 0:
        gainers = sorted(movers, key=lambda x: x[1], reverse=True)[:5]
        losers  = sorted(movers, key=lambda x: x[1])[:5]
        emb = discord.Embed(title="🔍 Market Scan Complete",
                            description=f"Scanned `{len(WATCHLIST)}` pairs | No new signal",
                            color=0x4A90D9, timestamp=datetime.now(timezone.utc))
        emb.add_field(name="🚀 Top Gainers", value="\n".join(f"`{s}` {p:+.2f}%" for s,p in gainers) or "—", inline=True)
        emb.add_field(name="💥 Top Losers",  value="\n".join(f"`{s}` {p:+.2f}%" for s,p in losers)  or "—", inline=True)
        emb.set_footer(text="Next scan 5 min • CryptoScalp Pro")
        await channel.send(embed=emb)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Scan #{scan_count} selesai | Signals: {signals_sent}")

# ════════════════════════════════════════════════════════
#   SCHEDULED TASK
# ════════════════════════════════════════════════════════

@tasks.loop(seconds=SCAN_INTERVAL)
async def auto_scan():
    await run_scanner()

@auto_scan.before_loop
async def before_scan():
    await bot.wait_until_ready()
    print("⚡ Bot ready — scan pertama langsung dimulai!")

# ════════════════════════════════════════════════════════
#   COMMANDS
# ════════════════════════════════════════════════════════

@bot.command(name="signal")
async def cmd_signal(ctx, symbol: str = None, tf: int = 5):
    if not symbol:
        await ctx.send("❌ Contoh: `!signal BTCUSDT 15`"); return
    symbol = symbol.upper()
    if not symbol.endswith("USDT"): symbol += "USDT"
    tf = tf if tf in [5, 15, 60] else 5
    msg = await ctx.send(f"⏳ Menganalisa **{symbol}** TF{tf}M...")
    async with aiohttp.ClientSession() as session:
        ticker = await fetch_ticker(session, symbol)
        if not ticker:
            await msg.edit(content=f"❌ `{symbol}` tidak ditemukan di Bybit."); return
        result = await analyze(session, symbol, tf)
    if not result:
        await msg.edit(content="❌ Data tidak cukup untuk analisa."); return
    conf, direction, reasons, ind, atr = result
    entry = ticker["mark"]
    sl, tp1, tp2, tp3, rr = calc_targets(direction, entry, atr, ticker)
    lev = suggest_lev(conf, (atr/entry*100) if entry else 1)
    emb = build_embed(symbol, tf, direction, entry, sl, tp1, tp2, tp3,
                      rr, conf, lev, ind, reasons, ticker)
    await msg.edit(content=f"📊 Analisa **{symbol}** TF{tf}M:", embed=emb)

@bot.command(name="scan")
async def cmd_scan(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Admin only."); return
    await ctx.send("🔍 Memulai scan manual...")
    await run_scanner()

@bot.command(name="history")
async def cmd_history(ctx, limit: int = 10):
    if not signal_history:
        await ctx.send("📭 Belum ada sinyal."); return
    emb = discord.Embed(title="📜 Signal History", color=0x7289DA)
    rows = []
    for s in signal_history[:min(limit, 20)]:
        e = "🟢" if s["direction"] == "LONG" else "🔴"
        rows.append(f"{e} `{s['symbol']}` TF{s['tf']}M | {s['direction']} | {s['confidence']}% | {s['time']}")
    emb.description = "\n".join(rows)
    await ctx.send(embed=emb)

@bot.command(name="price")
async def cmd_price(ctx, symbol: str = "BTCUSDT"):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"): symbol += "USDT"
    msg = await ctx.send(f"⏳ Mengambil harga **{symbol}**...")
    async with aiohttp.ClientSession() as session:
        t = await fetch_ticker(session, symbol)
    if not t:
        await msg.edit(content=(
            f"❌ Gagal mengambil data `{symbol}`.\n"
            f"Pastikan nama pair benar, contoh: `!price BTC` atau `!price BTCUSDT`\n"
            f"Cek log Railway untuk detail error."
        ))
        return
    pct = t["pct24h"]
    emb = discord.Embed(title=f"💹 {symbol}", color=0x00FF88 if pct>=0 else 0xFF3366,
                        timestamp=datetime.now(timezone.utc))
    emb.add_field(name="Mark",    value=f"`{t['mark']}`",          inline=True)
    emb.add_field(name="Last",    value=f"`{t['last']}`",          inline=True)
    emb.add_field(name="24h",     value=f"`{pct:+.2f}%`",          inline=True)
    emb.add_field(name="High 24h",value=f"`{t['high24h']}`",       inline=True)
    emb.add_field(name="Low 24h", value=f"`{t['low24h']}`",        inline=True)
    emb.add_field(name="Funding", value=f"`{t['funding']:+.4f}%`", inline=True)
    emb.add_field(name="Vol 24h", value=f"`{t['vol24h']:,.0f}`",   inline=False)
    await msg.edit(content=None, embed=emb)

@bot.command(name="watchlist")
async def cmd_watchlist(ctx):
    emb = discord.Embed(title="👁️ Watchlist", color=0xFFA500,
                        description=f"**{len(WATCHLIST)}** pairs dipantau di Bybit Perp")
    for i, chunk in enumerate([WATCHLIST[i:i+10] for i in range(0,len(WATCHLIST),10)]):
        emb.add_field(name=f"Group {i+1}", value=" ".join(f"`{s}`" for s in chunk), inline=False)
    await ctx.send(embed=emb)

@bot.command(name="status")
async def cmd_status(ctx):
    uptime = int(time.time()-bot_start_time) if bot_start_time else 0
    h,m,s = uptime//3600, (uptime%3600)//60, uptime%60
    emb = discord.Embed(title="🤖 Bot Status", color=0x00FF88)
    emb.add_field(name="Uptime",       value=f"`{h}h {m}m {s}s`",     inline=True)
    emb.add_field(name="Scans",        value=f"`{scan_count}`",         inline=True)
    emb.add_field(name="Signals",      value=f"`{signal_count}`",       inline=True)
    emb.add_field(name="Pairs",        value=f"`{len(WATCHLIST)}`",     inline=True)
    emb.add_field(name="Interval",     value=f"`{SCAN_INTERVAL}s`",     inline=True)
    emb.add_field(name="Ping",         value=f"`{round(bot.latency*1000)}ms`", inline=True)
    await ctx.send(embed=emb)

@bot.command(name="help")
async def cmd_help(ctx):
    emb = discord.Embed(title="📖 CryptoScalp Pro", color=0x7289DA,
                        description="Bot sinyal scalping Bybit Perpetual")
    for n, d in [
        ("!signal [PAIR] [TF]", "Analisa manual. Contoh: `!signal BTCUSDT 15`"),
        ("!price [PAIR]",        "Harga real-time. Contoh: `!price ETHUSDT`"),
        ("!history [limit]",     "Sinyal terakhir"),
        ("!watchlist",           "Daftar pair dipantau"),
        ("!status",              "Status & statistik bot"),
        ("!scan",                "Trigger scan manual (admin)"),
    ]:
        emb.add_field(name=f"`{n}`", value=d, inline=False)
    emb.set_footer(text="Auto-scan 5 menit • TF 5M & 15M • Bybit Perp")
    await ctx.send(embed=emb)

# ════════════════════════════════════════════════════════
#   EVENTS
# ════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = time.time()
    print("="*50)
    print(f"  Bot online : {bot.user}")
    print(f"  Channel    : {SIGNAL_CHANNEL}")
    print(f"  Interval   : {SCAN_INTERVAL}s")
    print(f"  Pairs      : {len(WATCHLIST)}")
    print(f"  Port       : {PORT}")
    print("="*50)
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching,
                                  name=f"{len(WATCHLIST)} pairs | Bybit Scalp"))
    if not auto_scan.is_running():
        auto_scan.start()
    ch = bot.get_channel(SIGNAL_CHANNEL)
    if ch:
        emb = discord.Embed(
            title="🚀 CryptoScalp Pro — Online!",
            description=(f"Bot aktif memantau `{len(WATCHLIST)}` pair Bybit Perp!\n\n"
                         f"• 🕐 Scan otomatis setiap `{SCAN_INTERVAL}s`\n"
                         f"• 📊 TF: `5M` & `15M`\n"
                         f"• 🎯 Min confidence: `55%`\n"
                         f"• ⚙️ RSI, MACD, EMA, BB, StochRSI, Volume, Divergence\n\n"
                         f"Ketik `!help` untuk daftar command."),
            color=0x00FF88, timestamp=datetime.now(timezone.utc))
        emb.set_footer(text="CryptoScalp Pro • DYOR • Not Financial Advice")
        await ch.send(embed=emb)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Kurang argumen. Ketik `!help`.")
    elif not isinstance(error, commands.CommandNotFound):
        print(f"Error: {error}")

# ════════════════════════════════════════════════════════
#   MAIN — bot + web server jalan bersamaan
# ════════════════════════════════════════════════════════

async def main():
    await start_web_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    print("Starting CryptoScalp Pro (Railway mode)...")
    asyncio.run(main())
