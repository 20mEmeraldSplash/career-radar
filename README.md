# Career Radar

一次性运行 Google + Greenhouse，结果写到同一份文件：

```bash
source .venv/bin/activate
python main.py
```

输出：

- `output/jobs.json`
- `output/jobs.html`

每条职位带 `source`: `google` 或 `greenhouse`。

## Google

- `google.py`: Google Careers 搜索页解析
- 可调：`QUERY`、`LOCATIONS`、`TOP_N`、`MAX_YEARS_EXCLUSIVE`

## Greenhouse

- `greenhouse.py`: Greenhouse 公开 Job Board API
- 也可单独跑：`python greenhouse.py`（会写到 `greenhouse_jobs.*`）
- 筛选：估计人数 `> 200`、标题为 Software Engineer / Senior Software Engineer、过去 24 小时、地点为 US Remote / San Diego；若 JD 提到 years of experience，则不可超过 5 年
- 时间字段区分 `first_published` / `updated_at`（`published_at` + `date_source`）
- 可调：`COMPANIES`、`MIN_EMPLOYEES`、`LOOKBACK_HOURS`、`MAX_YEARS`、`LOCATIONS`
