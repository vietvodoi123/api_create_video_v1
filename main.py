import os
import uuid
import shutil
import requests
import json
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv

# Import moviepy
from moviepy import AudioFileClip, ImageClip, TextClip, CompositeVideoClip
from moviepy.audio.AudioClip import concatenate_audioclips

# Import Google Drive API
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()

app = FastAPI()

# Giới hạn số tác vụ background chạy đồng thời bằng semaphore
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", 2))
video_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# CSDL giả lưu trạng thái video (in-memory)
video_db = {}

# Đường dẫn font chữ
font_path = "fonts/static/Roboto-Regular.ttf"

class VideoRequest(BaseModel):
    task_id: str
    story_name: str
    chapter: str
    image_path: str
    audio_urls: List[str]
    webhook_url: str | None = None

@app.post("/create_video")
async def create_video(video_req: VideoRequest, background_tasks: BackgroundTasks):
    """
    Nhận yêu cầu tạo video và chạy background task.
    Sử dụng BackgroundTasks của FastAPI để schedule quá trình xử lý video.
    """
    video_id = str(uuid.uuid4())
    video_db[video_id] = {
        "status": "queued",
        "url": None,
        "task_id": video_req.task_id,
        "webhook_url": video_req.webhook_url
    }
    # Schedule background task
    background_tasks.add_task(process_video, video_id, video_req.story_name, video_req.chapter, video_req.image_path, video_req.audio_urls)
    return {"video_id": video_id, "status": "queued", "task_id": video_req.task_id}

@app.get("/video_status/{video_id}")
async def video_status(video_id: str):
    if video_id not in video_db:
        raise HTTPException(status_code=404, detail="Video not found")
    return video_db[video_id]

async def process_video(video_id: str, story_name: str, chapter: str, image_path: str, audio_urls: List[str]):
    """
    Xử lý video và upload lên Google Drive.
    Giới hạn concurrency bằng cách sử dụng semaphore.
    """
    async with video_semaphore:
        try:
            video_db[video_id]["status"] = "processing"
            # --- Tải file audio ---
            audio_files = []
            for idx, url in enumerate(audio_urls):
                temp_audio_path = f"temp_audio_{video_id}_{idx}.mp3"
                await download_file(url, temp_audio_path)
                audio_files.append(temp_audio_path)

            # --- Ghép audio ---
            audio_clips = [AudioFileClip(file) for file in audio_files]
            final_audio = concatenate_audioclips(audio_clips)
            final_audio_path = f"final_audio_{video_id}.mp3"
            final_audio.write_audiofile(final_audio_path)

            # --- Tạo video ---
            image_clip = ImageClip(image_path, duration=final_audio.duration)
            text_clip1 = TextClip(text=story_name, font=font_path, font_size=30, color='white', duration=final_audio.duration)
            text_clip2 = TextClip(text=chapter, font=font_path, font_size=20, color='white', duration=final_audio.duration)

            # Định vị text ở vị trí center dưới cùng
            w, h = image_clip.size
            text_clip1 = text_clip1.with_position(("center", h - 80))
            text_clip2 = text_clip2.with_position(("center", h - 40))

            video_clip = CompositeVideoClip([image_clip, text_clip1, text_clip2]).with_duration(final_audio.duration)
            video_clip.audio = final_audio

            # Xuất video ra file
            video_file = f"video_{video_id}.mp4"
            video_clip.write_videofile(video_file, fps=24)

            # --- Upload lên Google Drive ---
            video_url = await upload_to_googledrive(video_file)
            video_db[video_id]["status"] = "completed"
            video_db[video_id]["url"] = video_url
            send_webhook(video_id)

        except Exception as e:
            video_db[video_id]["status"] = f"error: {str(e)}"
        finally:
            # Xóa file tạm
            for file in audio_files:
                if os.path.exists(file):
                    os.remove(file)
            if os.path.exists(final_audio_path):
                os.remove(final_audio_path)
            if os.path.exists(video_file):
                os.remove(video_file)

async def download_file(url: str, dest_path: str):
    """Tải file từ URL và lưu vào dest_path (sử dụng requests, nên cân nhắc chạy blocking code trong thread pool nếu cần)"""
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(dest_path, 'wb') as out_file:
            shutil.copyfileobj(response.raw, out_file)
    else:
        raise Exception(f"Failed to download file from {url}")

async def upload_to_googledrive(file_path: str) -> str:
    """ Upload file lên Google Drive """
    service_account_info_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_INFO")
    if not service_account_info_str:
        raise Exception("Environment variable GOOGLE_SERVICE_ACCOUNT_INFO not set")
    service_account_info = json.loads(service_account_info_str)

    FOLDER_ID = '1Xz3fU5KTOwXsibOyAqxhR8StvJkIYYJD'
    SCOPES = ['https://www.googleapis.com/auth/drive']
    credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    service = build('drive', 'v3', credentials=credentials)

    file_metadata = {'name': os.path.basename(file_path), 'parents': [FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype='video/mp4')
    uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    file_id = uploaded_file.get('id')
    permission = {'type': 'anyone', 'role': 'reader'}
    service.permissions().create(fileId=file_id, body=permission).execute()

    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

def send_webhook(video_id: str):
    """ Gửi webhook khi video hoàn thành """
    task_info = video_db.get(video_id)
    if not task_info or not task_info.get("webhook_url"):
        return

    data = {"task_id": task_info["task_id"], "status": task_info["status"], "video_url": task_info["url"]}
    try:
        response = requests.post(task_info["webhook_url"], json=data)
        response.raise_for_status()
        print(f"✅ Webhook gửi thành công: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Lỗi webhook: {e}")
