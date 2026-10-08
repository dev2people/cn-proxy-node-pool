# 中国代理节点批量检测与维护池 (CN Proxy Node Pool)

本项目用于批量过滤、检测中国境内代理节点的可用性，自动输出可用代理列表并支持通过 GitHub Actions 定时运行。

---

## 🚀 功能特性

1. **智能过滤中国节点**：
   - 自动扫描并解析 `input-raw-data` 目录下的代理数据源（支持 `proxies.json`、标准 JSON 与 TXT 格式）。
   - 提取 `country` 为中国（`CN` / `China` / `中国`）的代理。
   - 规范化协议格式（自动识别 HTTP / HTTPS / SOCKS4 / SOCKS5，支持多协议拆分扩展）。

2. **多阶段两级连通性检测**：
   - **第一阶段（TCP 握手检测）**：先对代理 IP 与端口发起异步 TCP 握手，快速跳过已关闭或无法路由的死节点，大幅节省检测耗时。
   - **第二阶段（网络连通性实测）**：通过代理分别向以下主流厂商的网络连通性探针发起 HTTP 请求：
     - **小米**：`http://connect.rom.miui.com/generate_204`（状态码 204 / 200）
     - **华为**：`http://connectivitycheck.platform.hicloud.com/generate_204`（状态码 204 / 200）
     - **微软 / Windows**：`http://www.msftconnecttest.com/connecttest.txt`（状态码 200，内容包含 `Microsoft Connect Test`）
     - **苹果 / Apple**：`http://captive.apple.com/hotspot-detect.html`（状态码 200，内容包含 `Success`）

3. **标准 URL 格式输出**：
   - 成功通过检测的节点以 URL 格式写入 `filter-data/allnode-url.txt`，每行一个节点，例如：
     ```text
     http://101.132.249.207:3129
     socks5://101.5.21.225:7894
     socks4://101.132.252.152:8008
     ```
   - 附带输出结构化详情文件 `filter-data/allnode-details.json`，记录每个探针的延迟（ms）与响应状态。

4. **高并发与可配置性**：
   - 超时时间默认 **5 秒**。
   - 并发数默认 **16 并发**。
   - 支持通过**环境变量**或**命令行参数**自由指定并发数、超时时间与判定策略。

5. **GitHub Actions 自动化**：
   - 配置了定时调度工作流 `.github/workflows/check-proxies.yml`，每 6 小时自动获取最新节点、执行检测并推送结果至代码仓库。

---

## 🛠️ 安装与使用

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 获取最新原始代理数据

```bash
bash 0-get-raw-node1.sh
```

### 3. 运行检测

#### 直接运行（默认 16 并发，5 秒超时）：

```bash
python main.py
```

#### 通过环境变量指定并发数与超时时间：

```bash
CONCURRENCY=32 TIMEOUT=5 python main.py
```

#### 通过命令行参数指定：

```bash
python main.py --concurrency 32 --timeout 5 --policy any
```

### 参数说明

| 命令行参数 | 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `--concurrency`, `-c` | `CONCURRENCY` | `16` | 检测并发数 |
| `--timeout`, `-t` | `TIMEOUT` | `5` | 握手与请求超时时间（秒） |
| `--policy`, `-p` | `SUCCESS_POLICY` | `any` | 成功判定策略：`any`（任一探针成功）或 `all`（全部4个探针成功） |
| `--input-dir`, `-i` | `INPUT_DIR` | `input-raw-data` | 原始节点数据所在目录 |
| `--output-file`, `-o` | `OUTPUT_FILE` | `filter-data/allnode-url.txt` | 输出节点 URL 文本路径 |
| `--details-file` | `DETAILS_FILE` | `filter-data/allnode-details.json` | 输出详细检测结果 JSON 路径 |

---

## 🤖 GitHub Actions 自动化

工作流文件位于 [`.github/workflows/check-proxies.yml`](.github/workflows/check-proxies.yml)：
- **定时触发**：默认每 6 小时自动执行一次（`cron: '0 */6 * * *'`）。
- **手动触发**：支持在 GitHub Actions 界面点击 `Run workflow` 手动指定并发数、超时和判定策略。
- **自动同步**：运行完毕后自动提交并推送 `filter-data/` 目录变动到当前分支。

---

## 📁 目录结构

```text
cn-proxy-node-pool/
├── .github/
│   └── workflows/
│       └── check-proxies.yml   # GitHub Actions 定时检测工作流
├── 0-get-raw-node1.sh          # 下载原始代理数据的脚本
├── filter-data/
│   ├── allnode-url.txt         # 检测成功的节点 URL 列表 (每行一个)
│   └── allnode-details.json    # 详细检测结果 (含延迟、探针状态)
├── input-raw-data/
│   └── proxies.json            # 原始代理数据
├── main.py                     # 核心检测程序
├── requirements.txt            # Python 依赖清单
└── README.md                   # 项目使用文档
```
