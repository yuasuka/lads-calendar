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

def fetch_api(key, pages=4):
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

def match_existing(pools, videos):
    n = 0
    for p in pools:
        if p.get("pred") or p.get("pvUrl"): continue
        s = d(p["start"]); key = norm(p["name"])
        c = [v for v in videos if key and key in norm(v["title"]) and not excluded(v["title"])
             and -WINDOW_AFTER <= (s - v["published"].date()).days <= WINDOW_BEFORE]
        if not c: continue
        v = pick(c)
        p["pvUrl"] = "https://www.youtube.com/watch?v=" + v["id"]
        if p.get("pvSource") != "youtube" and p.get("pv"):      # 保留原本（簡中服／B站）查證值
            p["pvCn"], p["pvCnTime"] = p.get("pv"), p.get("pvTime")
        p["pv"] = v["published"].date().isoformat(); p["pvTime"] = v["published"].strftime("%H:%M"); p["pvSource"] = "youtube"
        n += 1
    return n

# 支援：6月17日更新後~7月2日上午4:59 ／ 8月5日上午5:00~8月14日上午4:59 ／ 2025年12月31日更新後~2026年1月21日上午 4:59
DATE_RE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:更新後|(?:上午|凌晨)\s*\d{1,2}[:：]\d{2})\s*[~～\-–—至到]+\s*"
                     r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:上午|凌晨)?\s*\d{1,2}[:：]\d{2}")
BRACKET = re.compile(r"「([^」]+)」")

def parse_period(desc, pub):
    m = DATE_RE.search(desc or "")
    if not m: return None
    y1, m1, d1, y2, m2, d2 = m.groups()
    sy = int(y1) if y1 else pub.year
    s = date(sy, int(m1), int(d1))
    if not y1 and (s - pub.date()).days < -60: s = date(sy + 1, int(m1), int(d1))
    ey = int(y2) if y2 else s.year
    e = date(ey, int(m2), int(d2))
    if e < s and not y2: e = date(ey + 1, int(m2), int(d2))
    return s, e

def parse_title(title):
    m = BRACKET.search(title)
    if not m: return None
    inner = m.group(1).lstrip("#")
    parts = re.split(r"[·‧•・]", inner, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", inner.strip())

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
        if excluded(t) or not re.search(r"PV公開|即將開啟", t) or not re.search(r"五星|思念|許願|生日", t): continue
        pt = parse_title(t); per = parse_period(v["desc"], v["published"])
        if not pt or not per: continue
        chars, name = pt; s, e = per
        rerun = "復刻" in t; kind = guess_kind(t, v["desc"])
        url = "https://www.youtube.com/watch?v=" + v["id"]
        dup = next((p for p in pools if not p.get("pred") and p["start"] == s.isoformat() and p["rerun"] == rerun
                    and (p["kind"] == kind or (chars and chars in p.get("chars", "")))), None)
        if dup:
            if not dup.get("pvUrl"): dup["pvUrl"] = url
            continue
        if (today - s).days > PENDING_LOOKBACK: continue
        if any(p.get("pvUrl") == url for p in pools): continue
        pools.append(dict(name=name, kind=kind, rerun=rerun, chars="" if kind == "混池" else chars,
                          start=s.isoformat(), end=e.isoformat(), pv=v["published"].date().isoformat(),
                          pvTime=v["published"].strftime("%H:%M"), pred=False, pending=True, pvSource="youtube",
                          pvUrl=url, id=f"{s.isoformat()}-yt{v['id']}"))
        added += 1
    return added

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
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    linked = match_existing(pools, videos); new = find_pending(pools, videos, today)
    pools.sort(key=lambda p: (p["start"], p["rerun"], p["name"]))
    data["updated"] = today.isoformat()
    changed = before != json.dumps(data, ensure_ascii=False, sort_keys=True)
    if changed: json.dump(data, open(a.pools, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"影片 {len(videos)} 支；本次新連結 {linked}；新增待確認池 {new}；{'已更新' if changed else '無變更'} pools.json")

if __name__ == "__main__":
    main()
