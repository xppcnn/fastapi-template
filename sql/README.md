# sql/ 目录说明

本目录存放 Alembic 离线导出的 SQL 文件，**仅用于无数据库访问权限的环境**（需要 DBA 手动导入的场景）。

## 生成方式

```bash
# 导出全链 SQL（含 alembic_version 版本记录，导入后与自动管理状态一致）
uv run alembic upgrade head --sql > upgrade.sql

# 导入目标环境
psql -h <host> -U <user> -d <dbname> -f upgrade.sql
```

## 注意事项

- 正常开发环境用自动管理即可（`alembic upgrade head`），不需要本目录
- 导入前先确认目标环境当前版本（`alembic current`），只导入更新的 SQL
- 完整的使用说明见根目录 [README](../README.md)
