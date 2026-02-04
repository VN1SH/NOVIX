# NOVIX Docker 部署（精简版）

本指南仅保留 Docker 运行所需的最短步骤。

## 1. 前置条件

- 已安装 **Docker** 与 **Docker Compose**。
- 本仓库使用外部网络 `npm`（给 Nginx Proxy Manager 等使用）：
  ```bash
  docker network create npm
  ```

## 2. 配置（最小化）

- 后端环境变量（可选）：
  ```bash
  cp backend/.env.example backend/.env
  ```
  按需填写 API Key。

- NPM 配置（可选，镜像/私有源）：
  ```bash
  npm config set registry https://registry.npmmirror.com
  ```
  仅在构建前端时需要。

## 3. 构建并启动

```bash
docker compose up -d --build
```

## 4. 常用检查

```bash
docker compose ps
```

前端默认由容器内 Nginx 提供，后端 API 默认监听 8000 端口（容器内）。
