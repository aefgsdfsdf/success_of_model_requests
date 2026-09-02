# 模型请求成功率监控

这是一个基于 FastAPI、PyMySQL 和原生前端的模型请求成功率监控系统。

系统读取 `oneapi.logs` 表的 `id`、`created_at`、`type`、`model_name` 和 `group` 字段。`type=2` 表示成功，`type=5` 表示失败。任务每分钟只按 `id` 增量读取新日志，不补历史数据；统计只保留最近一小时。页面支持分组筛选、倍率展示、模型总览和最近 60 分钟折线图。

## 一、部署前提

- Windows 10/11 或 Linux
- Python 3.10 或更高版本
- 运行机器可以访问 MySQL 地址和端口
- 数据库账号可以读取 `logs`，并创建/写入/更新汇总表

## 二、配置数据库

复制模板并编辑 `.env`：

Windows PowerShell：

```powershell
Copy-Item .env.example .env
notepad .env
```

Linux/macOS：

```bash
cp .env.example .env
nano .env
```

配置示例：

```env
DB_HOST=你的MySQL地址
DB_PORT=9009
DB_USER=你的数据库账号
DB_PASSWORD=你的数据库密码
DB_NAME=oneapi
DB_TIMESTAMP_UNIT=s
APP_PORT=8080
DB_INCREMENT_BATCH_SIZE=10000
DB_MAX_BATCHES_PER_TICK=1
DB_INITIAL_SEED_ROWS=0
```

`created_at` 当前使用秒级 Unix 时间戳，因此 `DB_TIMESTAMP_UNIT` 必须是 `s`。`DB_INITIAL_SEED_ROWS=0` 表示首次启动从当前最新日志开始，只统计启动之后的新请求。

不要分享 `.env`，也不要把它提交到 Git。`.gitignore` 已默认忽略它。

## 三、Windows 部署

```powershell
cd "项目所在目录"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m backend.main
```

如果 PowerShell 不允许激活脚本，可以直接使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend.main
```

浏览器访问 `http://127.0.0.1:8080/`。停止服务请在终端按 `Ctrl+C`。

## 四、Linux 部署

```bash
cd /path/to/ban-2
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m backend.main
```

后台运行：

```bash
nohup .venv/bin/python -m backend.main > server.log 2>&1 &
```

## 五、首次启动和数据范围

程序会自动创建：

- `model_group_minute_stats`：按分钟、分组、模型保存成功和失败数量
- `aggregation_checkpoints`：保存最后处理的 `logs.id`

首次启动时会清理旧汇总，并把断点定位到当前最大日志 ID，不扫描、不补历史数据。之后每分钟只读取新日志，汇总表只保留最近一小时。

刚启动没有新请求时页面为空是正常现象；产生请求并等一个分钟任务周期后即可看到数据。

## 六、页面使用

- 左侧可以选择所有分组或具体分组。
- 分组倍率显示为 `bailian x1`、`claude code x0.205`、`codex x0.035`、`codex2 x0.029`、`default x1`。
- 模型卡片显示成功率、总请求数、成功数和失败数。
- 点击“查看详情”查看该模型最近 60 分钟的折线图。
- 鼠标靠近折线或数据点时显示时间和成功率。
- 没有请求的分钟显示为空，不代表请求失败。

## 七、数据库权限

应用账号至少需要以下权限，具体主机范围请按安全策略收紧：

```sql
GRANT SELECT ON oneapi.logs TO 'monitor_user'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE ON oneapi.* TO 'monitor_user'@'%';
```

如果不允许应用自动建表，可由管理员提前创建汇总表后，再授予对应表的 `SELECT/INSERT/UPDATE/DELETE` 权限。

## 八、低负载设计

- 原始日志只通过主键 `id` 增量读取。
- 不按时间扫描 2000 万历史日志。
- 每分钟默认只读取一批新日志。
- 汇总表只保留最近一小时。
- 页面只访问汇总表，不直接查询 `logs`。
- 统计和断点在同一事务中更新，失败重试不会重复累加。

## 九、故障排查

端口被占用时：

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

可以结束对应进程，或把 `.env` 中的 `APP_PORT` 改成其他端口。

页面只有标题或没有模型时：

- 确认服务从项目目录启动。
- 浏览器执行 `Ctrl+F5`。
- 确认 `/api/groups` 和 `/api/models` 可以返回 JSON。
- 确认启动后已经产生新日志，并等待一个分钟周期。

数据库网络检查：

```powershell
Test-NetConnection 你的MySQL地址 -Port 9009
```

## 十、分享清单

分享时包含源代码、`requirements.txt`、`.env.example` 和本说明即可。不要包含 `.env`、真实密码、`work/*.log`、虚拟环境、`__pycache__` 或临时数据库驱动。
