import network
import urequests
import time
import ujson
from machine import Pin, I2C
import ssd1306
from c import CHARACTER_DATA  # 16x16中文字點陣字庫
import gc  # 加在文件開頭的 import 部分

# === Wi-Fi Settings ===
SSID = 'C3PO-phone'
PASSWORD = 'iamthewifi'

# === MOENV API Settings ===
API_KEY = 'e8dd42e6-9b8b-43f8-991e-b3dee723a52d'
MOENV_URL = f'http://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={API_KEY}&limit=1000&sort=ImportDate%20desc&format=CSV'  # 改用 http

# === OLED Initialization ===
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=400000)
oled_width = 128
oled_height = 64
oled = ssd1306.SSD1306_I2C(oled_width, oled_height, i2c)

# === Telegram Settings ===
BOT_TOKEN = '7995794320:AAH83WwN-5CVgfBBMAEkWC7K3kxXuQaH83M'
CHAT_ID = '7643071691'
TELEGRAM_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print('Connecting to Wi-Fi', end='')
        wlan.connect(SSID, PASSWORD)
        while not wlan.isconnected():
            print('.', end='')
            time.sleep(0.5)
        print(' Connected!')
        print('IP:', wlan.ifconfig()[0])

def get_top10_aqi():
    try:
        print('Fetching AQI data...')
        gc.collect()  # 清理記憶體
        response = None
        try:
            response = urequests.get(MOENV_URL, stream=True)  # 使用串流模式
            if response.status_code != 200:
                print(f"Error: HTTP {response.status_code}")
                return []
            
            # 逐行處理數據
            lines = []
            header_found = False
            sitename_index = -1
            aqi_index = -1
            aqi_data = []
            
            while True:
                line = response.raw.readline()
                if not line:
                    break
                    
                try:
                    line = line.decode().strip()
                    if not header_found:
                        if 'sitename' in line:
                            cols = line.split(',')
                            sitename_index = cols.index('sitename')
                            aqi_index = cols.index('aqi')
                            header_found = True
                        continue
                    
                    if header_found and line:
                        fields = line.split(',')
                        if len(fields) > max(sitename_index, aqi_index):
                            sitename = fields[sitename_index].strip()
                            sitename = sitename.replace('（', '(').replace('）', ')')
                            try:
                                aqi = int(fields[aqi_index].strip())
                                aqi_data.append((sitename, aqi))
                            except (ValueError, IndexError):
                                continue
                except Exception as e:
                    print(f"Line processing error: {e}")
                    continue
                    
            aqi_data.sort(key=lambda x: x[1])
            return aqi_data[:10]
            
        except Exception as e:
            print(f"Request error: {e}")
            return []
        finally:
            if response:
                response.close()
            gc.collect()  # 再次清理記憶體
                
    except Exception as e:
        print('Error fetching AQI:', e)
        return []

# === 中文字點陣繪製函式 ===
def draw_character(data, x, y):
    """繪製 16x16 字型到 OLED 上"""
    for j in range(16):
        for byte_index in range(2):
            byte_val = data[j * 2 + byte_index]
            for i in range(8):
                if byte_val & (1 << (7 - i)):
                    oled.pixel(x + byte_index * 8 + i, y + j, 1)

def is_chinese_char(ch):
    # 僅判斷中文 unicode 範圍，確保括號由 oled.text() 處理
    return ('\u4e00' <= ch <= '\u9fff')

def split_text_by_type(text):
    """拆分字串成中文和非中文片段，括號會被當成非中文分離"""
    if not text:
        return []
    result = []
    current_type = is_chinese_char(text[0])
    buffer = text[0]
    for ch in text[1:]:
        ch_type = is_chinese_char(ch)
        if ch_type == current_type:
            buffer += ch
        else:
            result.append((buffer, current_type))
            buffer = ch
            current_type = ch_type
    result.append((buffer, current_type))
    return result

def get_text_width_mixed(text):
    """計算混合文字的總像素寬度"""
    spacing_chinese = 2
    char_width_chinese = 16 + spacing_chinese
    spacing_english = 1
    char_width_english = 8 + spacing_english
    
    total_width = 0
    parts = split_text_by_type(text)
    for seg, is_ch in parts:
        if is_ch:
            total_width += len(seg) * char_width_chinese
        else:
            total_width += len(seg) * char_width_english
    return total_width

def display_text_mixed(text, start_x=0, start_y=0):
    """固定顯示混合中英文字，中文字用點陣字，非中文字用oled.text()"""
    spacing_chinese = 2
    char_width_chinese = 16 + spacing_chinese
    spacing_english = 1
    x_offset = start_x

    parts = split_text_by_type(text)
    for seg, is_ch in parts:
        if is_ch:
            for ch in seg:
                if ch in CHARACTER_DATA:
                    draw_character(CHARACTER_DATA[ch], x_offset, start_y)
                x_offset += char_width_chinese
        else:
            oled.text(seg, x_offset, start_y + 4)  # 英文稍微往下對齊，看起來舒服點
            x_offset += len(seg) * (8 + spacing_english)

# ---
# 關鍵修改部分: slide_aqi_display_with_pause 函數
# ---
def slide_aqi_display_with_pause(aqi_list, title, pause_time=1, slide_speed=0.02, slide_steps=4):
    """
    依序顯示每個城市的AQI資訊，每個停留指定時間，然後滑動到下一個，新項目也停留。
    aqi_list: 包含 (sitename, aqi) 元組的列表
    title: 跑馬燈的標題
    pause_time: 每個項目停留的時間 (秒)
    slide_speed: 滑動每個像素的延遲時間
    slide_steps: 每次滑動的像素數
    """
    num_items = len(aqi_list)
    if num_items == 0:
        oled.fill(0)
        display_text_mixed(title, 0, 0)
        display_text_mixed("無資料", 0, 20)
        oled.show()
        time.sleep(5)
        return

    current_item_index = 0
    line_height = 16 # 中文字元的高度，用於換行

    while True:
        # 獲取當前和下一個要顯示的項目資料
        current_idx = current_item_index
        next_idx = (current_item_index + 1) % num_items

        current_site, current_aqi = aqi_list[current_idx]
        next_site, next_aqi = aqi_list[next_idx]

        # 格式化顯示文字 
        current_line1_text = f'{current_idx + 1}.{current_site}'
        current_line2_text = f'AQI: {current_aqi}'
        
        next_line1_text = f'{next_idx + 1}.{next_site}'
        next_line2_text = f'AQI: {next_aqi}'

        # --- 第一階段: 顯示當前項目並停留 ---
        oled.fill(0)
        display_text_mixed(title, 0, 0) # 固定標題
        display_text_mixed(current_line1_text, 0, 20) # 第一行
        display_text_mixed(current_line2_text, 0, 20 + line_height) # 第二行 (Y座標往下移一行)
        oled.show()
        time.sleep(pause_time) # 停留 1 秒

        # --- 第二階段: 滑動動畫 ---
        # 計算需要滑動的總距離 (OLED 寬度)
        slide_distance = oled_width 

        for i in range(0, slide_distance + 1, slide_steps):
            oled.fill(0)
            display_text_mixed(title, 0, 0) # 固定標題

            # 當前項目向左滑出
            display_text_mixed(current_line1_text, 0 - i, 20)
            display_text_mixed(current_line2_text, 0 - i, 20 + line_height)
            
            # 下一個項目從右邊滑入
            display_text_mixed(next_line1_text, oled_width - i, 20)
            display_text_mixed(next_line2_text, oled_width - i, 20 + line_height)
            
            oled.show()
            time.sleep(slide_speed)
        
        # 更新索引，進入下一個迴圈時會顯示下一個項目
        current_item_index = next_idx

def url_encode(text):
    """Simple URL encoding for MicroPython"""
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
    encoded = ""
    for char in str(text):
        if char in safe:
            encoded += char
        else:
            # Convert to UTF-8 bytes and percent encode
            for byte in char.encode('utf-8'):
                encoded += f"%{byte:02X}"
    return encoded

def send_to_telegram(message):
    """Send message to Telegram with error handling"""
    if not message:
        print('Error: Empty message')
        return False
    
    try:
        payload = {
            'chat_id': CHAT_ID,
            'text': message
        }
        headers = {'Content-Type': 'application/json'}
        
        print('Sending to Telegram...')
        response = urequests.post(TELEGRAM_URL, 
                                data=ujson.dumps(payload).encode('utf-8'),
                                headers=headers)
        
        print('Response status:', response.status_code)
        success = response.status_code == 200
        response.close()
        return success
        
    except Exception as e:
        print('Error sending to Telegram:', e)
        return False

def format_aqi_message(aqi_list):
    """Format AQI data with better presentation"""
    try:
        lines = ['空氣品質最佳城市排名：']
        for idx, (site, aqi) in enumerate(aqi_list, 1):
            lines.append(f'{idx}. {site} (AQI: {aqi})')
        return '\n'.join(lines)
    except Exception as e:
        print('Error formatting:', e)
        return "Error: Could not format AQI data"

# --- 主程式 ---
gc.collect()
connect_wifi()
last_update = 0

while True:
    try:
        current_time = time.time()
        if current_time - last_update >= 1800:  # 30分鐘更新一次
            if (data := get_top10_aqi()):
                message = format_aqi_message(data)
                if send_to_telegram(message):
                    print("Data sent successfully")
                slide_aqi_display_with_pause(data, 'AQI最佳城市', 1, 0.02, 4)
                last_update = current_time
        time.sleep(1)
    except Exception as e:
        print('Error in main loop:', e)
        time.sleep(60)
