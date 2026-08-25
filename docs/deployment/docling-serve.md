# docling-serve 部署

## 启动

```bash
# 1. 准备 API key(写入 .env 或环境变量)
export DOCLING_SERVE_API_KEY="<random-secret>"

# 2. 启动
docker compose -f docker-compose.docling.yml up -d
```

默认行为对齐本项目配置：

| 配置 | 值 | 说明 |
|------|-----|------|
| CPU 推理 | `DOCLING_DEVICE=cpu` | 初期 CPU 足够 |
| 线程 | `DOCLING_NUM_THREADS=4` | 单文档内部并行 |
| 并发 worker | `DOCLING_SERVE_ENG_LOC_NUM_WORKERS=2` | Local Engine 线程池，勿简单对齐 CPU 核数 |
| 单文件上限 | 50MB | 与上传链路限制对齐 |
| 页数上限 | 500 | 防恶意超长文档 |
| API key | `DOCLING_SERVE_API_KEY` | 必填；调用时带 `X-Api-Key` 头 |

## 冒烟清单

```bash
# 1. 健康检查
curl -s http://localhost:5001/health

# 2. 示例 PDF 转换(同步端点验证)
curl -X POST http://localhost:5001/v1/convert/file \
  -H "X-Api-Key: $DOCLING_SERVE_API_KEY" \
  -F "files=@sample.pdf" \
  -F "to_formats=md" -F "to_formats=json" -F "do_ocr=true"

# 3. 异步任务流(与业务代码相同的路径)
curl -X POST http://localhost:5001/v1/convert/file/async \
  -H "X-Api-Key: $DOCLING_SERVE_API_KEY" \
  -F "files=@sample.pdf" -F "to_formats=md" -F "to_formats=json"
# 记录返回的 task_id → 轮询 /v1/status/poll/{task_id} → 取 /v1/result/{task_id}
```

## 模型说明(务必读)

- 官方镜像**内置默认模型**(layout + TableFormer)，标准管线**不需要**运行时下载，也**不要**设置 `DOCLING_SERVE_ARTIFACTS_PATH`
- **扫描件 OCR 需实测**：`do_ocr=true` 时若报缺模型(如 easyocr/rapidocr)，按
  [docling-serve models.md](https://github.com/docling-project/docling-serve/blob/main/docs/models.md)
  方式 2/3/4 预下载模型并设置 `DOCLING_SERVE_ARTIFACTS_PATH`；一旦设置该路径，**默认模型(layout/tableformer)也必须放进同一目录**(下载命令：`docling-tools models download layout tableformer <ocr-engine>`)
- 国内网络下载模型时设 `HF_ENDPOINT=https://hf-mirror.com`

## 常见坑

- 配置一律用**环境变量**，不要用 CLI flag(uvicorn `--workers>1`/`--reload` 时 CLI flag 失效)
- 任务状态是**节点内存态**(Local Engine)：serve 重启 → 进行中任务丢失；业务侧已做 404 兜底(标 failed 可重触发)
- 多副本部署(非 RQ 引擎)需要粘性会话；要水平扩展切 `DOCLING_SERVE_ENG_KIND=rq` + Redis，业务代码无需改动

## 与主服务联通

- docker 内网：`DOCLING_SERVE_BASE_URL=http://docling:5001`
- 本机调试：`DOCLING_SERVE_BASE_URL=http://localhost:5001`
- 不要把 docling 端口暴露到公网；内网 + API key 足够