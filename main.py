import os
import uuid
import shutil
import requests
import json
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List

# Import các lớp từ moviepy theo API phiên bản mới
from moviepy import AudioFileClip, ImageClip, TextClip, CompositeVideoClip
from moviepy.audio.AudioClip import concatenate_audioclips

# Import cho Google Drive API
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = FastAPI()

# CSDL giả lưu trạng thái video (video_id -> {status: ..., url: ...})
video_db = {}

# Giả sử bạn có Roboto-Regular.ttf trong thư mục fonts/static
font_path = "fonts/static/Roboto-Regular.ttf"

def send_webhook(video_id: str):
    """Gửi webhook khi video hoàn thành"""
    task_info = video_db.get(video_id)
    if not task_info or not task_info.get("webhook_url"):
        return  # Không có webhook thì bỏ qua

    data = {
        "task_id": task_info["task_id"],
        "status": task_info["status"],
        "video_url": task_info["url"],
    }

    try:
        response = requests.post(task_info["webhook_url"], json=data)
        response.raise_for_status()
        print(f"✅ Webhook gửi thành công: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Lỗi khi gửi webhook: {e}")


class VideoRequest(BaseModel):
    task_id:str
    story_name: str  # Tên của bộ truyện
    chapter: str     # Tập thứ bao nhiêu (có thể là số hoặc chuỗi)
    image_path: str  # Đường dẫn hình ảnh (file ảnh)
    audio_urls: List[str]  # Mảng các URL audio
    webhook_url: str | None = None  # Webhook có thể có hoặc không

@app.post("/create_video")
async def create_video(video_req: VideoRequest, background_tasks: BackgroundTasks):
    video_id = str(uuid.uuid4())
    # Khởi tạo trạng thái video là "processing"
    video_db[video_id] = {
        "status": "processing",
        "url": None,
        "task_id": video_req.task_id,
        "webhook_url": video_req.webhook_url  # Lưu webhook nếu có
    }

    # Thêm task chạy background
    background_tasks.add_task(
        process_video,
        video_id,
        video_req.story_name,
        video_req.chapter,
        video_req.image_path,
        video_req.audio_urls
    )
    return {"video_id": video_id,"status": "processing", "url": None ,"task_id":video_req.task_id }

@app.get("/video_status/{video_id}")
async def video_status(video_id: str):
    if video_id not in video_db:
        return {"error": "Video not found"}
    return video_db[video_id]

def download_file(url: str, dest_path: str):
    """Tải file từ URL và lưu vào dest_path"""
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(dest_path, 'wb') as out_file:
            shutil.copyfileobj(response.raw, out_file)
        return dest_path
    else:
        raise Exception("Failed to download file from url: " + url)

def upload_to_googledrive(file_path: str) -> str:
    """
    Upload file lên Google Drive sử dụng service account.
    Lấy thông tin credentials từ biến môi trường GOOGLE_SERVICE_ACCOUNT_INFO.
    """
    # Lấy thông tin credentials từ biến môi trường (JSON string)
    service_account_info_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_INFO")
    if not service_account_info_str:
        raise Exception("Environment variable GOOGLE_SERVICE_ACCOUNT_INFO not set")
    service_account_info = json.loads(service_account_info_str)

    # ID của thư mục trên Google Drive mà bạn muốn upload file vào
    FOLDER_ID = '1Xz3fU5KTOwXsibOyAqxhR8StvJkIYYJD'
    SCOPES = ['https://www.googleapis.com/auth/drive']
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=SCOPES)
    service = build('drive', 'v3', credentials=credentials)

    file_metadata = {
        'name': os.path.basename(file_path),
        'parents': [FOLDER_ID] if FOLDER_ID else []
    }
    media = MediaFileUpload(file_path, mimetype='video/mp4')
    uploaded_file = service.files().create(
        body=file_metadata, media_body=media, fields='id'
    ).execute()

    file_id = uploaded_file.get('id')

    # Cấp quyền truy cập công khai cho file (nếu cần)
    permission = {
        'type': 'anyone',
        'role': 'reader'
    }
    service.permissions().create(
        fileId=file_id, body=permission
    ).execute()

    # Trả về đường dẫn xem file trên Google Drive
    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

def process_video(video_id: str, story_name: str, chapter: str, image_path: str, audio_urls: List[str]):
    try:
        # --- Bước 1: Tải các file audio về ---
        audio_files = []
        for idx, url in enumerate(audio_urls):
            temp_audio_path = f"temp_audio_{video_id}_{idx}.mp3"
            download_file(url, temp_audio_path)
            audio_files.append(temp_audio_path)

        # --- Bước 2: Ghép các audio thành 1 file duy nhất ---
        audio_clips = [AudioFileClip(file) for file in audio_files]
        final_audio = concatenate_audioclips(audio_clips)
        final_audio_path = f"final_audio_{video_id}.mp3"
        final_audio.write_audiofile(final_audio_path)

        # --- Bước 3: Tạo video ---
        # Tạo ImageClip từ hình ảnh với thời lượng bằng với audio ghép
        image_clip = ImageClip(image_path, duration=final_audio.duration)

        # Tạo text clip cho dòng đầu (font size 48)
        text_clip1 = TextClip(
            text=story_name,
            font=font_path, method='caption', size=(image_clip.w - 2*10, None),
            font_size=30,
            color='white',
            duration=final_audio.duration
        )

        # Tạo text clip cho dòng thứ hai (font size 25)
        text_clip2 = TextClip(
            text=chapter,
            font=font_path,
            font_size=20, size=(image_clip.w - 2*10, None),
            color='white',
            duration=final_audio.duration
        )
        # Xác định vị trí của các text clip (góc dưới bên trái)
        margin = 10  # lề cách biên trái và dưới 10 pixel
        w, h = image_clip.size
        line1_height = text_clip1.h
        line2_height = text_clip2.h
        text_clip1.pos = lambda t: (margin, h - line1_height - line2_height - margin * 2)
        text_clip2.pos = lambda t: (margin, h - line2_height - margin)

        # Ghép ImageClip và TextClip thành video
        video_clip = CompositeVideoClip([image_clip, text_clip1, text_clip2]).with_duration(final_audio.duration)
        video_clip.audio = final_audio

        # Xuất video ra file
        video_file = f"video_{video_id}.mp4"
        video_clip.write_videofile(video_file, fps=24)

        # --- Bước 4: Upload video lên Google Drive ---
        video_url = upload_to_googledrive(video_file)
        video_db[video_id]["status"] = "completed"
        video_db[video_id]["url"] = video_url
        send_webhook(video_id)  # Gửi webhook

    except Exception as e:
        video_db[video_id]["status"] = f"error: {str(e)}"
    finally:
        # --- Xoá các file tạm thời ---
        for file in audio_files:
            if os.path.exists(file):
                os.remove(file)
        if os.path.exists(final_audio_path):
            os.remove(final_audio_path)
        if os.path.exists(video_file):
            os.remove(video_file)
