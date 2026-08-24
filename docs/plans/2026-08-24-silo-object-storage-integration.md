# SILO 对象存储集成（基础层）

日期：2026-08-24
目标：为 FastAPI 项目接入本地 SILO（S3 兼容对象存储），本阶段只做**客户端 + 配置 + 存储服务基础**，不实现业务上传接口。

## 背景

- SILO 已通过 docker-compose 在本地跑通（9000 S3 API / 9001 控制台，`pgsty/silo:RELEASE.2026-08-06T00-00-00Z`，凭据 `silo-admin`/`silo-admin`）。
- 模型层已预留 `DocumentVersion.object_key`、`sha256`、`size_bytes`、`UploadSession` 字段，但代码中尚无任何 S3 客户端。
- SILO 完全兼容 S3 API，Python 侧用官方 `minio` SDK（当前最新 7.2.20，需 Python ≥3.9，项目 3.12 满足）。

## 调用方式（核心示例）

```python
from minio import Minio
from datetime import timedelta
import io

client = Minio(
    "localhost:9000",          # endpoint，不含协议前缀
    access_key="silo-admin",
    secret_key="silo-admin",
    secure=False,              # 本地未开 TLS；生产建议 True
)

# 建桶（幂等）
if not client.bucket_exists("documents"):
    client.make_bucket("documents")

# 上传字节流 / 文件
client.put_object("documents", "doc/xxx.pdf",
                  io.BytesIO(data), length=len(data),
                  content_type="application/pdf")
client.fput_object("documents", "doc/xxx.pdf", "/path/to/x.pdf")

# 下载
resp = client.get_object("documents", "doc/xxx.pdf")
data = resp.read()
resp.release_conn()
client.fget_object("documents", "doc/xxx.pdf", "/local/path/x.pdf")

# 元数据 / 删除
stat = client.stat_object("documents", "doc/xxx.pdf")  # size / content_type / etag
client.remove_object("documents", "doc/xxx.pdf")

# 预签名 URL（浏览器直传/直取，不经后端）
client.presigned_put_object("documents", "doc/yyy.pdf", expires=timedelta(minutes=15))
client.presigned_get_object("documents", "doc/yyy.pdf", expires=timedelta(hours=1))
```

## 关键设计点

1. **同步 SDK × 异步 FastAPI**：
   - 纯计算/短调用（presign 生成、`stat_object`、`bucket_exists`）可同步调用。
   - 网络传输类（`put_object` / `get_object` / `fput_object`）用 `await asyncio.to_thread(...)` 包装，避免阻塞事件循环。
2. **配置入 `Settings`**（pydantic-settings，对齐 `app/core/config.py`）：
   - `silo_endpoint`（默认 `localhost:9000`；应用容器化后改 `silo:9000`）
   - `silo_root_user` / `silo_root_password`（默认 `silo-admin`）
   - `silo_bucket`（默认 `documents`）
   - `silo_secure`（默认 False）
3. **客户端放 `app/core/object_storage.py`**（新建，对标 `core/database.py` 持有 engine 的职责）：模块级 `Minio` client + `ensure_bucket()` + async 包装的 upload/download/delete/presign helper。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | 加 `minio>=7.2.20` |
| `.env.example` | 补 `SILO_*` 配置项 |
| `app/core/config.py` | 加 silo 配置字段 |
| `app/core/object_storage.py` | 新建：client + `ensure_bucket()` + async helper |
| `README.md` | 加一段 Python 调用示例 |

## 验证

- `docker compose up -d` 后 silo 为 healthy；
- 单元级验证：`ensure_bucket()` 幂等建桶；上传一个对象 → `stat_object` 读到正确 `size`/`content_type` → 下载字节一致 → presigned URL 可访问 → `remove_object` 删除；
- `uv run pytest` 与 `uv run mypy app/` 通过。

## 暂不实现（后续独立 task）

文档版本上传/下载业务链路：创建 `DocumentVersion` → presigned 直传 → 回填 `object_key`/`sha256`/`size_bytes` → `UploadSession` 状态流转。需要时再按本基础层扩展。
