#!/bin/bash
# NOVIX Frontend Startup Script for Linux/Mac

export LANG=${LANG:-en_US.UTF-8}
export LC_ALL=${LC_ALL:-en_US.UTF-8}

echo "Starting NOVIX Frontend..."
echo "正在启动 NOVIX 前端..."

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    echo "正在安装依赖..."
    npm install
fi

echo ""
echo "Starting development server at http://novix.dmxy.site"
echo "开发服务器启动于 http://novix.dmxy.site"
echo ""
echo "Make sure backend is running at http://novix.dmxy.site/api"
echo "请确保后端服务运行在 http://novix.dmxy.site/api"
echo ""

npm run dev
