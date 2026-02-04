# NOVIX 启动指南

## 快速开始

### 方式 1：使用 Python 启动脚本（推荐）

```bash
python start.py
```

或在 Windows 上双击：
```
start_simple.bat
```

### 方式 2：手动启动

**终端 1 - 启动后端服务：**
```bash
cd backend
python -m app.main
```

后端将在 http://novix.dmxy.site/api 启动

**终端 2 - 启动前端开发服务器：**
```bash
cd frontend
npm run dev
```

前端将在 http://novix.dmxy.site 启动

## 访问应用

启动后，访问以下地址：

- **前端界面**：http://novix.dmxy.site
- **后端 API**：http://novix.dmxy.site/api
- **API 文档**：http://novix.dmxy.site/api/docs （FastAPI Swagger UI）

## 配置 API Keys

首次使用需要配置 LLM API Keys：

1. 编辑 `backend/.env` 文件
2. 填入您的 API Keys：
   ```env
   OPENAI_API_KEY=sk-your-key-here
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   DEEPSEEK_API_KEY=your-deepseek-key-here
   ```

3. 或使用 Mock 模式进行演示（无需真实 Key）：
   ```env
   NOVIX_LLM_PROVIDER=mock
   ```

## 常见问题

### Q: 后端无法启动
A: 检查以下几点：
- Python 版本是否 3.10+：`python --version`
- 依赖是否已安装：`pip install -r backend/requirements.txt`
- 8000 端口是否被占用

### Q: 前端无法连接到后端
A:
- 确保后端已启动在 http://novix.dmxy.site/api
- 检查防火墙设置
- 查看浏览器控制台（F12）的错误信息

### Q: 如何修改端口
A:
- **后端**：编�� `backend/.env`，修改 `PORT=8000`
- **前端**：编辑 `frontend/vite.config.js`，修改 `port: 3000`

### Q: 如何使用不同的 LLM 提供商
A: 编辑 `backend/config.yaml`，修改 `llm.default_provider` 和各个 agent 的 provider 配置

## 项目结构

```
NOVIX-main/
├── backend/              # FastAPI 后端
│   ├── app/             # 应用代码
│   ├── config.yaml      # 配置文件
│   ├── .env             # 环境变量
│   └── requirements.txt  # Python 依赖
├── frontend/            # React 前端
│   ├── src/            # 源代码
│   ├── package.json    # Node.js 依赖
│   └── vite.config.js  # Vite 配置
├── data/               # 项目数据（自动创建）
├── start.py            # Python 启动脚本
└── start_simple.bat    # Windows 启动脚本
```

## 下一步

1. 创建新项目
2. 配置角色卡和世界观卡
3. 开始写作会话
4. 查看 API 文档了解更多功能

## 获取帮助

- 查看 `STARTUP_TROUBLESHOOTING.md` 了解更多故障排除方法
- 查看 `CLAUDE.md` 了解项目架构
- 访问 API 文档：http://novix.dmxy.site/api/docs
- 提交 Issue：https://github.com/unitagain/NOVIX/issues
