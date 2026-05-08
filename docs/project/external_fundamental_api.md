# External Fundamental API Consumer

本项目作为内部消费者时，A 股基本面上下文默认优先调用外部 HTTP API：

```bash
GET http://192.168.50.88:5999/fundamentals/{ts_code}?as_of=YYYY-MM-DD
Authorization: Bearer $EXTERNAL_FUNDAMENTAL
Accept: application/json
```

## 默认配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `EXTERNAL_FUNDAMENTAL_CONTEXT` | `true` | 外部基本面 API 总开关 |
| `EXTERNAL_FUNDAMENTAL_API_BASE_URL` | `http://192.168.50.88:5999` | 外部基本面 API base URL |
| `EXTERNAL_FUNDAMENTAL` | 空 | Bearer token，必须在运行进程环境中配置 |
| `EXTERNAL_FUNDAMENTAL_API_TOKEN_ENV` | `EXTERNAL_FUNDAMENTAL` | token 所在环境变量名 |
| `EXTERNAL_FUNDAMENTAL_API_TIMEOUT_MS` | `10000` | 单次 HTTP 请求超时 |
| `EXTERNAL_FUNDAMENTAL_API_MAX_RETRIES` | `1` | 失败后的额外重试次数 |
| `EXTERNAL_FUNDAMENTAL_API_CACHE_TTL_SECONDS` | `300` | 按 `stock_code + as_of` 缓存 |
| `EXTERNAL_FUNDAMENTAL_API_FAIL_OPEN` | `true` | 外部 API 失败时回退内置基本面链路 |

## 重启要求

Web、调度和 CLI 分析进程只在启动或配置重载时读取环境变量。修改 `EXTERNAL_FUNDAMENTAL_CONTEXT`、`EXTERNAL_FUNDAMENTAL_API_BASE_URL`、`EXTERNAL_FUNDAMENTAL` 或超时配置后，需要重启对应进程。

## 故障判断

分析日志中搜索 `source_chain`：

- `source_chain[0].provider = external_fundamental_api`：已经走外部 HTTP API。
- `source_chain[0].provider = fundamental_bundle`：没有走外部 API，优先检查运行进程是否带有 token、base URL、开关配置，并确认服务已重启。
- 仅 `pe_ratio=None` 不一定代表接口缺字段；亏损股或底层估值源未提供 PE 时，PE 可合法为空，下游诊断应与“接口未返回字段”区分。

## 调用示例

```bash
curl -H "Authorization: Bearer $EXTERNAL_FUNDAMENTAL" \
  -H "Accept: application/json" \
  "http://192.168.50.88:5999/fundamentals/605100.SH?as_of=2026-05-08"
```
