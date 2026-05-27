#!/usr/bin/env python3
"""
Gyroflow 批量处理工具
使用方式：修改下方 CONFIG 配置后，运行 python3 gyroflow_batch.py
"""
import pyautogui, subprocess, time, os, re, json

pyautogui.FAILSAFE = False

# ============================================================
# 配置区 —— 每次使用前修改这里
# ============================================================
CONFIG = {
    # 预设文件路径（.gyroflow 文件，包含镜头校准+稳定参数+自动同步）
    "preset": "/Volumes/xiaomi13/gyzidonghua/grrofolw-json/24mm.gyroflow",
    
    # GCSV 运动数据存放目录
    "gcsv_dir": "//Volumes/xiaomi13/0519",
    
    # 素材根目录（DNG 序列帧文件夹所在位置）
    "base_dir": "//Volumes/xiaomi13/0519",
    
    # 要处理的素材名称列表
    # GCSV 文件自动匹配为: {gcsv_dir}/{素材名}.gcsv
    # DNG 文件夹自动匹配为: {base_dir}/{素材名}/
    "sequences": [
        "260519_183651_xiaomi13_24mm",
        "260519_183859_xiaomi13_24mm",
        "260519_184034_xiaomi13_24mm",
    ],
    
    # 运动数据「打开文件」按钮的屏幕坐标
    # 获取方法：处理到 Step 3 后用截图工具测量
    "btn_coords": (128, 697),
    
    # 是否需要在处理前先复制 GCSV 到临时目录（False=直接输入完整路径）
    "use_temp_copy": False,
    
    # 临时目录（use_temp_copy=True 时使用）
    "temp_dir": "/tmp/gyroflow_input",
}
# ============================================================

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def paste_text(text):
    """用剪贴板粘贴文本"""
    import pyperclip
    old = pyperclip.paste()
    pyperclip.copy(text)
    time.sleep(0.2)
    pyautogui.hotkey('command', 'v')
    time.sleep(0.5)
    pyperclip.copy(old)

def get_gyroflow_cpu():
    """获取 Gyroflow 进程 CPU 使用率"""
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for l in r.stdout.split('\n'):
        if 'Gyroflow' in l and 'grep' not in l and 'python' not in l:
            p = re.split(r'\s+', l)
            try: return float(p[2])
            except: pass
    return None

def wait_for_sync(timeout=60):
    """等待自动同步完成（监控 CPU）"""
    idle = 0
    waited = 0
    while waited < timeout:
        cpu = get_gyroflow_cpu()
        if cpu is not None and cpu < 3.0:
            idle += 1
            if idle >= 3:
                log(f"同步完成 (CPU={cpu}%)")
                return True
        else:
            idle = 0
        time.sleep(2)
        waited += 2
    log("⚠️ 同步超时")
    return False

def process_one(seq_name):
    """处理单个素材"""
    cfg = CONFIG
    dng = os.path.join(cfg["base_dir"], seq_name, f"{seq_name}-000010.dng")
    gcsv = os.path.join(cfg["gcsv_dir"], f"{seq_name}.gcsv")
    dng_dir = os.path.dirname(dng)
    
    # 检查文件
    if not os.path.exists(dng):
        log(f"⚠️ DNG 不存在: {dng}")
        return False
    if not os.path.exists(gcsv):
        log(f"⚠️ GCSV 不存在: {gcsv}")
        return False
    if not os.path.exists(cfg["preset"]):
        log(f"⚠️ 预设不存在: {cfg['preset']}")
        return False
    
    log(f"=== 处理: {seq_name} ===")
    
    # 关闭已有 Gyroflow
    subprocess.run(['pkill', '-9', '-f', 'Gyroflow'], capture_output=True)
    time.sleep(3)
    
    # ---- Step 1-2: 加载 DNG ----
    log("Step 1-2: 加载 DNG")
    subprocess.run(['open', '-a', 'Gyroflow']); time.sleep(8)
    subprocess.run(['open', '-a', 'Gyroflow', dng]); time.sleep(8)
    subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate']); time.sleep(1)
    subprocess.run(['osascript', '-e', 'tell application "System Events" to key code 36']); time.sleep(7)
    log("✅ DNG 加载完毕")
    
    # ---- Step 3: 加载配置预设 ----
    log("Step 3: 加载配置预设")
    subprocess.run(['open', '-a', 'Gyroflow', cfg["preset"]])
    time.sleep(12)
    log("✅ 预设加载完毕")
    
    # ---- Step 4: 加载运动数据 ----
    log("Step 4: 加载运动数据")
    btn_x, btn_y = cfg["btn_coords"]
    pyautogui.click(btn_x, btn_y)
    time.sleep(6)
    
    pyautogui.hotkey('command', 'shift', 'g')
    time.sleep(4)
    
    # 输入 GCSV 路径（逐字输入）
    if cfg["use_temp_copy"]:
        # 复制到临时目录后用字母选择
        os.makedirs(cfg["temp_dir"], exist_ok=True)
        tmp_gcsv = os.path.join(cfg["temp_dir"], "b_gyro.gcsv")
        subprocess.run(['cp', gcsv, tmp_gcsv])
        pyautogui.typewrite(cfg["temp_dir"], interval=0.1)
    else:
        # 直接输入完整 GCSV 路径
        pyautogui.typewrite(gcsv, interval=0.1)
    
    time.sleep(4)
    pyautogui.press('return'); time.sleep(3)   # 关闭前往文件夹
    pyautogui.press('return'); time.sleep(6)   # 确认选择文件
    log("✅ 运动数据加载完毕")
    
    # ---- Step 5: 等待自动同步 ----
    log("⏳ 等待自动同步...")
    wait_for_sync()
    
    # ---- Step 6: 保存项目 ----
    log("Step 6: 保存项目")
    subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate'])
    time.sleep(2)
    pyautogui.hotkey('command', 's'); time.sleep(4)
    pyautogui.press('return'); time.sleep(4)
    log("✅ 项目已保存")
    
    # ---- 验证 ----
    saved_files = [f for f in os.listdir(dng_dir) 
                   if f.endswith('.gyroflow') and not f.startswith('._')]
    if saved_files:
        latest = sorted(saved_files)[-1]
        fp = os.path.join(dng_dir, latest)
        with open(fp) as fh:
            d = json.load(fh)
        gs = d.get('gyro_source', {})
        has_gcsv = '.gcsv' in gs.get('filepath', '')
        has_lens = bool(d.get('calibration_data', {}).get('fisheye_params'))
        log(f"   文件: {latest}")
        log(f"   陀螺仪: {'✅ GCSV' if has_gcsv else '⚠️ 非GCSV'}")
        log(f"   镜头: {'✅' if has_lens else '⚠️ 缺失'}")
    else:
        log(f"⚠️ 未找到项目文件")
    
    return True


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    print("=" * 50)
    print("Gyroflow 批量处理工具")
    print("=" * 50)
    print(f"预设: {CONFIG['preset']}")
    print(f"素材目录: {CONFIG['base_dir']}")
    print(f"按钮坐标: {CONFIG['btn_coords']}")
    print(f"待处理: {len(CONFIG['sequences'])} 个素材")
    print()
    
    input("请确保：\n  1. 已切换到英文输入法\n  2. Gyroflow 已关闭\n  3. 辅助功能权限已授权\n\n按 Enter 开始...")
    
    success = 0
    failed = 0
    start_time = time.time()
    
    for seq in CONFIG["sequences"]:
        print()
        if process_one(seq):
            success += 1
        else:
            failed += 1
        time.sleep(5)
    
    elapsed = time.time() - start_time
    print()
    print("=" * 50)
    print(f"处理完成！成功: {success}, 失败: {failed}")
    print(f"总耗时: {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")
    print("=" * 50)
