更新方式（不要動你 GitHub 上現有的 pools.json，裡面有腳本填入的影片連結）
1. index.html                       -> 覆蓋倉庫根目錄的 index.html
2. scripts/update_pv.py             -> 覆蓋 scripts/update_pv.py
3. .github/workflows/update.yml     -> 覆蓋（排程已是每天台灣 11:30）
4. Actions -> Update PV links -> Run workflow
管理模式：網址後面加 ?admin=1；離開加 ?admin=0
