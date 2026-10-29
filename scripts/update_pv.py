#!/usr/bin/env python3
"""
自動更新 pools.json（台服官方 YouTube 頻道 @LoveandDeepSpace_tw）：
  1) 把 PV 影片連結（pvUrl）填入對應卡池，並以 YouTube 發布日期／時間作為台服 PV 首播時間
  2) 發現清單裡沒有的新卡池公告 -> 以 pending=true 加入（網頁標「待確認」，請人工核對）
資料來源：有環境變數 YT_API_KEY 就用 YouTube Data API，沒有就退回頻道 RSS。
用法：python scripts/update_pv.py [--mock videos.json] [--pools pools.json] [--today YYYY-MM-DD]
"""
import json, os, re, sys, argparse, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone, date

CHANNEL_ID = "UCLr5Ot_ycop6Lbl1N8WPY9Q"          # @LoveandDeepSpace_tw
TW = timezone(timedelta(hours=8))
WINDOW_BEFORE, WINDOW_AFTER = 14, 2             # PV 發布日需落在 開池日-14 ~ 開池日+2
PENDING_LOOKBACK = 14
EXCLUDE = ("BGM", "小劇場", "概念PV", "印象PV", "聯動", "基準測試", "直播", "前瞻", "科普", "公益", "Shorts")

def norm(s): return re.sub(r"[\W_]+", "", s or "")
def http_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lads-calendar/1.0"}), timeout=30) as r:
        return json.load(r)
def parse_ts(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TW)

def fetch_api(key, pages=12):
    pl = "UU" + CHANNEL_ID[2:]; out, token = [], ""
    for _ in range(pages):
        q = {"part": "snippet,contentDetails", "playlistId": pl, "maxResults": 50, "key": key}
        if token: q["pageToken"] = token
        j = http_json("https://www.googleapis.com/youtube/v3/playlistItems?" + urllib.parse.urlencode(q))
        for it in j.get("items", []):
            sn, cd = it["snippet"], it.get("contentDetails", {})
            out.append(dict(id=cd.get("videoId") or sn["resourceId"]["videoId"], title=sn["title"],
                            desc=sn.get("description", ""), published=parse_ts(cd.get("videoPublishedAt") or sn["publishedAt"])))
        token = j.get("nextPageToken", "")
        if not token: break
    return out

def fetch_rss():
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}
    with urllib.request.urlopen(urllib.request.Request("https://www.youtube.com/feeds/videos.xml?channel_id=" + CHANNEL_ID,
                                headers={"User-Agent": "lads-calendar/1.0"}), timeout=30) as r:
        root = ET.fromstring(r.read())
    return [dict(id=e.find("yt:videoId", ns).text, title=e.find("a:title", ns).text,
                 desc=(e.findtext("m:group/m:description", default="", namespaces=ns) or ""),
                 published=parse_ts(e.find("a:published", ns).text)) for e in root.findall("a:entry", ns)]

def load_mock(path):
    return [dict(id=v["id"], title=v["title"], desc=v.get("desc", ""), published=parse_ts(v["published"])) for v in json.load(open(path, encoding="utf-8"))]

def excluded(t): return any(w in t for w in EXCLUDE)
def pick(c):
    pref = [v for v in c if "PV公開" in v["title"] or "即將開啟" in v["title"]]
    return sorted(pref or c, key=lambda v: v["published"])[0]
def d(s): return date.fromisoformat(s)

def yurl(v): return "https://www.youtube.com/watch?v=" + v["id"]

def add_urls(p, vids):
    """把影片連結記進 pvUrl（第一支）與 pvUrls（兩支以上才記）。回傳是否有變動。"""
    vids = sorted(vids, key=lambda v: v["published"])
    urls = [yurl(v) for v in vids]
    old = (p.get("pvUrl"), p.get("pvUrls"))
    if p.get("pvUrl") and p["pvUrl"] not in urls: urls.insert(0, p["pvUrl"])
    p["pvUrl"] = urls[0]
    if len(urls) > 1: p["pvUrls"] = urls
    return old != (p.get("pvUrl"), p.get("pvUrls"))

def match_existing(pools, videos):
    n = 0
    for p in pools:
        if p.get("pred"): continue
        s = d(p["start"]); key = norm(p["name"])
        def in_window(v): return -WINDOW_AFTER <= (s - v["published"].date()).days <= WINDOW_BEFORE
        c = [v for v in videos if key and key in norm(v["title"]) and not excluded(v["title"]) and in_window(v)]
        # 復刻池的名稱常是活動名（如「逢光續歲」），官方影片標題卻寫卡名，且同一角色可能有好幾張生日卡
        # （沈星回 2024、2025 各一張，復刻時各有一支影片）-> 單人復刻池，標題含角色名與「復刻」者全部收進來
        if not c and p.get("rerun") and p.get("chars") and "／" not in p["chars"]:
            c = [v for v in videos if p["chars"] in v["title"] and "復刻" in v["title"]
                 and not excluded(v["title"]) and in_window(v)]
        if not c: continue
        first = pick(c)
        had = bool(p.get("pvUrl"))
        if not add_urls(p, c): continue
        if not had:                                        # 第一次連結時才補 PV 日期／時間
            if p.get("pvSource") != "youtube" and p.get("pv"):
                p["pvCn"], p["pvCnTime"] = p.get("pv"), p.get("pvTime")
            p["pv"] = first["published"].date().isoformat(); p["pvTime"] = first["published"].strftime("%H:%M"); p["pvSource"] = "youtube"
        n += 1
    return n

# 支援：6月17日更新後~7月2日上午4:59 ／ 8月5日上午5:00~8月14日上午4:59 ／ 2025年12月31日更新後~2026年1月21日上午 4:59
DATE_RE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:更新後|(?:上午|凌晨)\s*\d{1,2}[:：]\d{2})\s*[~～\-–—至到]+\s*"
                     r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:上午|凌晨)?\s*\d{1,2}[:：]\d{2}")
START_ONLY = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:上午|凌晨)\s*\d{1,2}[:：]\d{2}\s*起")
BRACKET = re.compile(r"「([^」]+)」")
BRACKET2 = re.compile(r"【([^】]+)】")
CHARS = ("沈星回", "黎深", "祁煜", "秦徹", "夏以晝")

def parse_period(desc, pub):
    """回傳 (開始, 結束, 結束日是否為推估)。生日池只寫「X月X日上午5:00起」時，結束日以 開始+7 天推估。"""
    m = DATE_RE.search(desc or "")
    if m:
        y1, m1, d1, y2, m2, d2 = m.groups()
        sy = int(y1) if y1 else pub.year
        s = date(sy, int(m1), int(d1))
        if not y1 and (s - pub.date()).days < -60: s = date(sy + 1, int(m1), int(d1))
        ey = int(y2) if y2 else s.year
        e = date(ey, int(m2), int(d2))
        if e < s and not y2: e = date(ey + 1, int(m2), int(d2))
        return s, e, False
    m = START_ONLY.search(desc or "")
    if m:
        y1, m1, d1 = m.groups()
        sy = int(y1) if y1 else pub.year
        s = date(sy, int(m1), int(d1))
        if not y1 and (s - pub.date()).days < -60: s = date(sy + 1, int(m1), int(d1))
        return s, s + timedelta(days=7), True
    return None

def parse_title(title):
    ms = BRACKET.findall(title) or BRACKET2.findall(title)
    if not ms: return None
    inner = ms[-1].lstrip("#")
    parts = re.split(r"[·‧•・]", inner, maxsplit=1)
    chars, name = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", inner.strip())
    if not chars:
        c = next((c for c in CHARS if c in title), "")
        chars = c
    return chars, name

def guess_kind(title, desc):
    t = title + desc
    if "日冕" in t: return "日卡"
    if "生日" in title: return "生日"
    if re.search(r"任選\s*[3三]\s*張|選\s*[3三]\s*張|自選三|混池", t): return "混池"
    return "月卡"

def find_pending(pools, videos, today):
    added = 0
    for v in videos:
        t = v["title"]
        if excluded(t) or is_story(t) or not re.search(r"PV公開|即將開啟", t) or not re.search(r"五星|思念|許願|生日", t): continue
        pt = parse_title(t); per = parse_period(v["desc"], v["published"])
        if not pt or not per: continue
        chars, name = pt; s, e, est = per
        rerun = "復刻" in t; kind = guess_kind(t, v["desc"])
        if est and kind != "生日": continue
        url = "https://www.youtube.com/watch?v=" + v["id"]
        dup = next((p for p in pools if not p.get("pred") and p["start"] == s.isoformat() and p["rerun"] == rerun
                    and (p["kind"] == kind or (chars and chars in p.get("chars", "")))), None)
        if dup:
            add_urls(dup, [v])
            continue
        if (today - s).days > PENDING_LOOKBACK: continue
        if any(p.get("pvUrl") == url for p in pools): continue
        pools.append(dict(name=name, kind=kind, rerun=rerun, chars="" if kind == "混池" else chars,
                          start=s.isoformat(), end=e.isoformat(), pv=v["published"].date().isoformat(),
                          pvTime=v["published"].strftime("%H:%M"), pred=False, pending=True, pvSource="youtube",
                          pvUrl=url, id=f"{s.isoformat()}-yt{v['id']}", **({"endEstimated": True} if est else {})))
        added += 1
    return added


# ================= 劇情活動（主線／分線／免費五星） =================
def story_title(t): return bool(re.search(r"主線|分線|篇章", t))
def is_story(t): return story_title(t) or ("免費" in t and bool(re.search(r"五星|思念", t)))

def find_story(pools, videos):
    """主線篇章、分線、免費五星活動：說明欄有「開始~結束」就建立一個『劇情』類別的活動，持續時間＝活動期間。
    一旦建立就不會被刪除；同開始日的多支影片會併進同一個活動。"""
    added = 0
    for v in sorted(videos, key=lambda x: x["published"]):
        t = v["title"]
        if excluded(t) or not is_story(t): continue
        per = parse_period(v["desc"], v["published"])
        if not per or per[2]: continue                      # 沒有明確結束日的不自動建立（交給下方「其他 PV」）
        s, e, _ = per
        pt = parse_title(t); chars, name = pt if pt else ("", re.sub(r"\s+", "", t)[:20])
        has_pv = "PV" in t.upper()
        dup = next((p for p in pools if p.get("kind") == "劇情" and p["start"] == s.isoformat()), None)
        if dup:
            add_urls(dup, [v])
            if has_pv and not dup.get("pv"):
                dup["pv"] = v["published"].date().isoformat(); dup["pvTime"] = v["published"].strftime("%H:%M"); dup["pvSource"] = "youtube"
            continue
        if any(yurl(v) in ([p.get("pvUrl")] + (p.get("pvUrls") or [])) for p in pools): continue
        pools.append(dict(name=name, kind="劇情", rerun=False, chars=chars, start=s.isoformat(), end=e.isoformat(),
                          pv=(v["published"].date().isoformat() if has_pv else None),
                          pvTime=(v["published"].strftime("%H:%M") if has_pv else None),
                          pred=False, pending=True, pvSource="youtube", pvUrl=yurl(v), id=f"{s.isoformat()}-yt{v['id']}"))
        added += 1
    return added

# ================= 其他官方 PV（沒有對應到卡池或劇情活動的影片） =================
PV_RE = re.compile(r"PV|MV|預告|實拍|概念短片", re.I)
EXCL_LIST = ("BGM", "基準測試", "直播", "科普", "公益")

def cat_of(t):
    if re.search(r"主線|分線|篇章|免費", t): return "劇情"
    if "生日" in t: return "生日"
    if re.search(r"版本|週年|盛典|公測|上線", t): return "版本"
    if re.search(r"思念|許願|系列活動|五星", t): return "卡池"
    return "其他"

def vid_of(u): return u.split("v=")[-1] if u and "v=" in u else ""

def collect_pvs(data, pools, videos):
    linked = set()
    for p in pools:
        for u in ([p.get("pvUrl")] + (p.get("pvUrls") or [])):
            if u: linked.add(vid_of(u))
    cur = {x["id"]: x for x in data.get("pvs", [])}
    for v in videos:
        t = v["title"]
        if v["id"] in linked or not PV_RE.search(t) or any(w in t for w in EXCL_LIST): continue
        cur[f"yt-{v['id']}"] = dict(id=f"yt-{v['id']}", title=re.sub(r"\s+", " ", t).strip(), date=v["published"].date().isoformat(),
                                    time=v["published"].strftime("%H:%M"), url=yurl(v), cat=cat_of(t))
    # 已被卡池／劇情活動收編的影片不再重複列在「其他 PV」；其餘一律保留，不會刪除
    data["pvs"] = sorted((x for x in cur.values() if vid_of(x["url"]) not in linked), key=lambda x: (x["date"], x.get("time") or ""))
    return len(data["pvs"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pools", default="pools.json"); ap.add_argument("--mock"); ap.add_argument("--today")
    a = ap.parse_args()
    today = d(a.today) if a.today else datetime.now(TW).date()
    if a.mock: videos = load_mock(a.mock)
    else:
        key = os.environ.get("YT_API_KEY", "").strip()
        try: videos = fetch_api(key) if key else fetch_rss()
        except Exception as ex:
            print("抓取失敗：", ex); sys.exit(1)
    if not videos: print("沒有取得任何影片，未變更。"); sys.exit(1)
    data = json.load(open(a.pools, encoding="utf-8")); pools = data["pools"]
    snap = lambda x: json.dumps({k: v for k, v in x.items() if k != "updated"}, ensure_ascii=False, sort_keys=True)
    before = snap(data)
    linked = match_existing(pools, videos); new = find_pending(pools, videos, today)
    story = find_story(pools, videos)
    npv = collect_pvs(data, pools, videos)
    pools.sort(key=lambda p: (p["start"], p["rerun"], p["name"]))
    changed = before != snap(data)
    if changed:
        data["updated"] = today.isoformat()
        json.dump(data, open(a.pools, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"影片 {len(videos)} 支；本次新連結 {linked}；新增待確認池 {new}；新增劇情活動 {story}；其他 PV 共 {npv} 支；{'已更新' if changed else '無變更'} pools.json")
    # ---- 診斷資訊（出問題時把下面幾行貼給我）----
    real = [p for p in pools if not p.get("pred")]
    have = [p for p in real if p.get("pvUrl")]
    vs = sorted(videos, key=lambda v: v["published"], reverse=True)
    print(f"[診斷] 非預測池 {len(real)} 個，其中已有影片連結 {len(have)} 個；劇情活動 {sum(1 for p in real if p.get('kind')=='劇情')} 個")
    print(f"[診斷] 影片涵蓋 {vs[-1]['published']:%Y-%m-%d} ~ {vs[0]['published']:%Y-%m-%d}")
    miss = [p for p in real if not p.get("pvUrl") and 0 <= (today - d(p["start"])).days <= 60 or
            (not p.get("pvUrl") and 0 <= (d(p["start"]) - today).days <= 14)]
    print("[診斷] 近期無連結的池：" + ("、".join(f"{p['name']}({p['start'][5:]})" for p in miss) or "無"))
    for p in miss:
        sd = d(p["start"])
        cand = [v for v in vs if -WINDOW_AFTER - 5 <= (sd - v["published"].date()).days <= WINDOW_BEFORE + 5 and "基準測試" not in v["title"]]
        print(f"[診斷] 「{p['name']}」（{p['start']}）找不到連結；開池前後的官方影片：" + ("" if cand else "（沒有）"))
        for v in cand[:10]:
            print(f"   {v['published']:%m/%d %H:%M} {v['title'][:60]}  [{v['id']}]")
    print("[診斷] 最新 8 支影片標題：")
    for v in vs[:8]:
        print(f"   {v['published']:%m/%d %H:%M} {v['title'][:50]}")

if __name__ == "__main__":
    main()
