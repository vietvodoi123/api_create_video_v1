import moviepy
from moviepy import AudioFileClip, ImageClip, TextClip, CompositeVideoClip

# Tải file audio và lấy thời lượng của nó
audio_clip = AudioFileClip("1.mp3")

# Tạo image clip với thời lượng bằng với audio
image_clip = ImageClip("1.jpg", duration=audio_clip.duration)

# Cung cấp đường dẫn tới font hợp lệ (định dạng OpenType)
font_path = "C:/Windows/Fonts/arial.ttf"

# Tạo text clip cho dòng đầu (font size 48)
text_clip1 = TextClip(
text="Dòng text lớn",
    font=font_path,
    font_size=48,
    color='white',
    duration=audio_clip.duration
)

# Tạo text clip cho dòng thứ hai (font size 25)
text_clip2 = TextClip(
    text="Dòng text nhỏ",
    font=font_path,
    font_size=25,
    color='white',
    duration=audio_clip.duration
)
# Xác định vị trí của các text clip (góc dưới bên trái)
margin = 10  # lề cách biên trái và dưới 10 pixel

# Lấy kích thước của ảnh nền (width, height)
w, h = image_clip.size

# Lấy chiều cao của từng text clip
line1_height = text_clip1.h
line2_height = text_clip2.h

# Đặt vị trí cho từng text clip bằng lambda trả về tọa độ (x, y)
# TextClip thứ nhất đặt phía trên text thứ hai
text_clip1.pos = lambda t: (margin, h - line1_height - line2_height - margin*2)
# TextClip thứ hai đặt ngay dưới, cách dưới 10 pixel
text_clip2.pos = lambda t: (margin, h - line2_height - margin)


# Ghép các clip lại với nhau và đặt thời lượng bằng với audio
video_clip = CompositeVideoClip([image_clip, text_clip1, text_clip2]).with_duration(audio_clip.duration)
# Gán audio cho video
video_clip.audio = audio_clip

# Xuất video với fps 24
video_clip.write_videofile("video_output.mp4", fps=24)
