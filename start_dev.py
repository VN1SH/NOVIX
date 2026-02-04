#!/usr/bin/env python3
"""
NOVIX 启动脚本 - 改进版本
支持开发模式（前端热重载）和生产模式
"""

import subprocess
import sys
import time
import os
import platform
import webbrowser

def check_python():
    """Check if Python 3.10+ is available"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print("[ERROR] Python 3.10+ is required")
        return False
    print(f"[OK] Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_node():
    """Check if Node.js 18+ is available"""
    try:
        result = subprocess.run(['node', '--version'], capture_output=True, text=True)
        version_str = result.stdout.strip()
        version_parts = version_str.lstrip('v').split('.')
        major = int(version_parts[0])
        if major < 18:
            print(f"[ERROR] Node.js 18+ is required, found {version_str}")
            return False
        print(f"[OK] Node.js {version_str}")
        return True
    except FileNotFoundError:
        print("[ERROR] Node.js is not installed or not in PATH")
        return False

def start_backend():
    """Start backend service"""
    print("\n[1/2] Starting backend service...")
    backend_dir = os.path.join(os.path.dirname(__file__), 'backend')

    if platform.system() == 'Windows':
        subprocess.Popen(
            ['cmd', '/k', 'python -m app.main'],
            cwd=backend_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    else:
        subprocess.Popen(
            ['python', '-m', 'app.main'],
            cwd=backend_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    print("  Backend: http://novix.dmxy.site/api")
    time.sleep(3)

def start_frontend():
    """Start frontend development server"""
    print("[2/2] Starting frontend development server...")
    frontend_dir = os.path.join(os.path.dirname(__file__), 'frontend')

    if platform.system() == 'Windows':
        subprocess.Popen(
            ['cmd', '/k', 'npm run dev'],
            cwd=frontend_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    else:
        subprocess.Popen(
            ['npm', 'run', 'dev'],
            cwd=frontend_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    print("  Frontend: http://novix.dmxy.site")

def main():
    print("=" * 60)
    print("  NOVIX - Context-Aware Novel Writing System")
    print("  Development Mode")
    print("=" * 60)
    print()

    # Check requirements
    print("Checking requirements...")
    if not check_python():
        sys.exit(1)
    if not check_node():
        sys.exit(1)

    print()

    # Start services
    try:
        start_backend()
        start_frontend()

        print()
        print("=" * 60)
        print("  Services started successfully!")
        print("=" * 60)
        print()
        print("Access URLs:")
        print("  Frontend:   http://novix.dmxy.site")
        print("  Backend:    http://novix.dmxy.site/api")
        print("  API Docs:   http://novix.dmxy.site/api/docs")
        print()
        print("Tip: Close the service windows to stop the services.")
        print()

        # Try to open browser
        time.sleep(2)
        try:
            webbrowser.open('http://novix.dmxy.site')
        except:
            pass

        # Keep script running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")

    except Exception as e:
        print(f"[ERROR] Failed to start services: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
