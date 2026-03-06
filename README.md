# 视频文案提取服务

从 YouTube（后续支持 Bilibili、抖音、TikTok）提取视频字幕和描述文案，供 AI 分析生成菜谱。

## Ubuntu 服务器部署

### 第一步：安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv ffmpeg
```

验证安装：

```bash
python3 --version   # 需要 3.10+
ffmpeg -version
```

### 第二步：上传代码到服务器

**方式 A：用 scp 上传（本机执行）**

```bash
scp -r /Users/chicheng/video-caption-service user@your-server-ip:~/video-caption-service
```

**方式 B：在服务器上用 git clone（需先推送到 Git）**

```bash
git clone https://github.com/your-repo/video-caption-service.git
```

### 第三步：安装 Python 依赖

```bash
cd ~/video-caption-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 第四步：启动服务

**前台运行（用于调试验证）：**

```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

**后台常驻运行：**

```bash
source venv/bin/activate
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > ~/video-caption-service/service.log 2>&1 &

# 查看日志
tail -f ~/video-caption-service/service.log
```

### 第五步：开放防火墙端口（如果有 ufw）

```bash
sudo ufw allow 8000
```

---

## 命令行验证

### 健康检查

```bash
curl http://your-server-ip:8000/health
# 期望返回: {"status":"ok"}
```

### 提取 YouTube 视频文案

```bash
curl -X POST http://your-server-ip:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
```

**返回格式：**

```json
{
  "transcript": "字幕/语音转文字内容...",
  "description": "视频描述文字...",
  "combined": "【视频描述】\n...\n\n【字幕/语音文字】\n...",
  "platform": "youtube"
}
```

---

## 管理服务

```bash
# 查看进程 PID
pgrep -f "uvicorn main:app"

# 停止服务
pkill -f "uvicorn main:app"

# 重启服务
pkill -f "uvicorn main:app"
sleep 1
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > ~/video-caption-service/service.log 2>&1 &
```

## API 文档

启动后浏览器访问 `http://your-server-ip:8000/docs` 查看交互式 API 文档。
