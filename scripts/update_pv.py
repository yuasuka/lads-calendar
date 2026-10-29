#!/usr/bin/env python3
"""
自動更新 pools.json：
  1) 到官方《戀與深空》台灣 YouTube 頻道抓影片清單
  2) 把 PV 影片連結（pvUrl）填入對應卡池；尚無 PV 日期者補上 YouTube 發布日期與時間
  3) 發現清單裡沒有的新卡池公告 -> 以 pending=true 加入（網頁會標「待確認」，請人工核對）

資料來源（擇一，優先順序）：
  - 環境變數 YT_API_KEY：YouTube Data API（官方、穩定，免費額度足夠）
  - 否則改讀頻道 RSS（免金鑰，但 YouTube 近來不穩定，且只有最新 15 支）

用法：python scripts/update_pv.py [--mock videos.json] [--pools pools.json]
"""
import json, os, re, sys, argparse, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone, date

CHANNEL_ID = "UCLr5Ot_ycop6Lbl1N8WPY9Q"          # 官方台灣頻道 @LoveandDeepSpace_tw
TW = timezone(timedelta(hours=8))
WINDOW_BEFORE, WINDOW_AFTER = 14, 2             # PV 發布日需落在 開池日-14 ~ 開池日+2
PENDING_LOOKBACK = 14                           # 只自動新增「開池日 >= 今天-14 天」的新池
EXCLUDE = ("BGM", "小劇場", "概念PV", "聯動", "印象PV", "基準測試", "直播", "前瞻", "科普", "公益", "Shorts")

def norm(s):
    return re.sub(r"[\W_]+", "", s or "")

def http_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lads-calendar/1.0"}), timeout=30) as r:
        return json.load(r)

def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TW)

def fetch_api(key, pages=4):
    pl = "UU" + CHANNEL_ID[2:]
    out, token = [], ""
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
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + CHANNEL_ID
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lads-calendar/1.0"}), timeout=30) as r:
        root = ET.fromstring(r.read())
    out = []
    for e in root.findall("a:entry", ns):
        out.append(dict(id=e.find("yt:videoId", ns).text, title=e.find("a:title", ns).text,
                        desc=(e.findtext("m:group/m:description", default="", namespaces=ns) or ""),
                        published=parse_ts(e.find("a:published", ns).text)))
    return out

def load_mock(path):
    return [dict(id=v["id"], title=v["title"], desc=v.get("desc", ""), published=parse_ts(v["published"])) for v in json.load(open(path, encoding="utf-8"))]

def excluded(title):
    return any(w in title for w in EXCLUDE)

def pick(cands):
    pref = [v for v in cands if "PV" in v["title"] or "即將開啟" in v["title"]]
    return sorted(pref or cands, key=lambda v: v["published"])[0]

def d(s): return date.fromisoformat(s)

def match_existing(pools, videos):
    n = 0
    for p in pools:
        if p.get("pred") or p.get("pvUrl"): continue
        s = d(p["start"]); key = norm(p["name"])
        cands = [v for v in videos if key and key in norm(v["title"]) and not excluded(v["title"])
                 and -WINDOW_AFTER <= (s - v["published"].date()).days <= WINDOW_BEFORE]
        if not cands: continue
        v = pick(cands)
        p["pvUrl"] = "https://www.youtube.com/watch?v=" + v["id"]
        p["ytDate"] = v["published"].date().isoformat()
        if not p.get("pv"):
            p["pv"] = v["published"].date().isoformat()
            p["pvTime"] = v["published"].strftime("%H:%M")
            p["pvSource"] = "youtube"
        n += 1
    return n

DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\s*更新後\s*[~～\-–—至到]+\s*(\d{1,2})月(\d{1,2})日\s*(?:上午|凌晨)?\s*\d{1,2}[:：]\d{2}")
BRACKET = re.compile(r"「([^」]+)」")

def parse_period(desc, pub):
    m = DATE_RE.search(desc or "")
    if not m: return None
    m1, d1, m2, d2 = map(int, m.groups())
    s = date(pub.year, m1, d1)
    if (s - pub.date()).days < -60: s = date(pub.year + 1, m1, d1)
    e = date(s.year, m2, d2)
    if e < s: e = date(s.year + 1, m2, d2)
    return s, e

def parse_title(title):
    m = BRACKET.search(title)
    if not m: return None
    inner = m.group(1).lstrip("#")
    parts = re.split(r"[·‧•・]", inner, maxsplit=1)
    chars, name = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", inner.strip())
    return chars, name

def guess_kind(title, desc):
    t = title + desc
    if "日冕" in t: return "日卡"
    if "生日" in title: return "生日"
    if any(w in t for w in ("混池", "三選", "選三", "五選三", "自選三")): return "混池"
    return "月卡"

def find_pending(pools, videos, today):
    added = 0
    for v in videos:
        t = v["title"]
        if excluded(t) or not re.search(r"PV公開|即將開啟", t) or not re.search(r"五星|思念|許願|生日", t): continue
        pt = parse_title(t)
        per = parse_period(v["desc"], v["published"])
        if not pt or not per: continue
        chars, name = pt; s, e = per
        rerun = "復刻" in t; kind = guess_kind(t, v["desc"])
        url = "https://www.youtube.com/watch?v=" + v["id"]
        # 已存在（同開池日、同復刻屬性，且角色或類型相符）-> 只補連結
        dup = next((p for p in pools if not p.get("pred") and p["start"] == s.isoformat() and p["rerun"] == rerun
                    and (p["kind"] == kind or (chars and chars in p.get("chars", "")))), None)
        if dup:
            if not dup.get("pvUrl"): dup["pvUrl"] = url
            continue
        if (today - s).days > PENDING_LOOKBACK: continue        # 太舊的未知池不自動新增
        if any(p.get("pvUrl") == url for p in pools): continue
        pools.append(dict(name=name, kind=kind, rerun=rerun, chars="" if kind == "混池" else chars,
                          start=s.isoformat(), end=e.isoformat(), pv=v["published"].date().isoformat(),
                          pvTime=v["published"].strftime("%H:%M"), pred=False, pending=True, pvSource="youtube",
                          pvUrl=url, ytDate=v["published"].date().isoformat(), id=f"{s.isoformat()}-yt{v['id']}"))
        added += 1
    return added

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pools", default="pools.json"); ap.add_argument("--mock")
    ap.add_argument("--today")
    a = ap.parse_args()
    today = d(a.today) if a.today else datetime.now(TW).date()
    if a.mock: videos = load_mock(a.mock)
    else:
        key = os.environ.get("YT_API_KEY", "").strip()
        try: videos = fetch_api(key) if key else fetch_rss()
        except Exception as ex:
            print("抓取失敗：", ex); sys.exit(1)
    if not videos:
        print("沒有取得任何影片，未變更。"); sys.exit(1)
    data = json.load(open(a.pools, encoding="utf-8")); pools = data["pools"]
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    linked = match_existing(pools, videos)
    new = find_pending(pools, videos, today)
    pools.sort(key=lambda p: (p["start"], p["rerun"], p["name"]))
    data["updated"] = today.isoformat()
    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    changed = before != after
    if changed:
        json.dump(data, open(a.pools, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"影片 {len(videos)} 支；本次新連結 {linked}；新增待確認池 {new}；{'已更新' if changed else '無變更'} pools.json")

if __name__ == "__main__":
    main()
