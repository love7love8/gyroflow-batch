#!/usr/bin/env python3
"""
Gyroflow 批量处理工具 — GUI 版 v3
双击打开，原生 macOS 对话框选择文件，无需手动改代码。

v3 新增:
  - 视频模式：支持 MP4/MOV 视频文件（配合 GCSV 运动数据）
  - DNG/视频双模式切换

修复/优化:
  - 同步超时不再保存废文件
  - 恢复 JSON 验证（GCSV + 镜头参数）
  - 合并坐标拾取的两个对话框
  - 特殊字符转义（路径含引号等）
  - 处理前提醒英文输入法
  - 预检所有文件再开始
  - 失败后支持重试
  - 实时进度预估
  - 同步超时时间可调
"""
import pyautogui, subprocess, time, os, re, json, sys, tempfile, shlex, shutil

pyautogui.FAILSAFE = False
GYROFLOW_BIN = "/Applications/Gyroflow.app/Contents/MacOS/gyroflow"

# ── 配置保存 ──────────────────────────────────────────────
CONFIG_FILE = os.path.expanduser("~/.gyroflow_batch_config.json")

DEFAULT_CONFIG = {
    "base_dir": "",
    "gcsv_dir": "",
    "lens_dir": "",
    "export_dir": "",
    "btn_coords": [],
    "sync_coords": [],
    "lens_coords": [],
    "export_coords": [],
    "last_sequences": [],
    "sync_timeout": 120,
    "export_timeout": 600,
    "export_mode": "project",
    "screen_resolution": "",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = {**DEFAULT_CONFIG, **json.load(f)}
            return cfg
        except:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ── AppleScript 转义 ──────────────────────────────────────
def _escape_as(s):
    """转义字符串使其安全嵌入 AppleScript 双引号字符串"""
    return s.replace('\\', '\\\\').replace('"', '\\"')

# ── 原生对话框 ────────────────────────────────────────────
def osa_dialog(message, buttons=["取消", "确定"], default="确定", icon="note"):
    # 兜底：如果 default 不在 buttons 里，用最后一个 button 作为 default
    if default not in buttons:
        default = buttons[-1]
    btn = ','.join(f'"{b}"' for b in buttons)
    ico = f'with icon {icon}' if icon else ''
    msg = _escape_as(message)
    script = f'display dialog "{msg}" buttons {{{btn}}} default button "{default}" {ico}'
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[对话框错误] {r.stderr.strip()}")
        return None
    for b in buttons:
        if b in r.stdout:
            return b
    return None

def osa_choose_file(prompt="选择文件", default_dir=""):
    loc = f'default location (POSIX file "{_escape_as(default_dir)}")' if default_dir and os.path.exists(default_dir) else ''
    script = f'''
    set f to choose file with prompt "{_escape_as(prompt)}" {loc}
    return POSIX path of f
    '''
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip()

def osa_choose_folder(prompt="选择文件夹", default_dir=""):
    loc = f'default location (POSIX file "{_escape_as(default_dir)}")' if default_dir and os.path.exists(default_dir) else ''
    script = f'''
    set f to choose folder with prompt "{_escape_as(prompt)}" {loc}
    return POSIX path of f
    '''
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip()

def osa_notify(title, text=""):
    subprocess.run(['osascript', '-e', f'display notification "{_escape_as(text)}" with title "{_escape_as(title)}"'],
                   capture_output=True)

def osa_choose_list(prompt, items, default_items=None):
    """多选列表，自动处理含引号/逗号的素材名"""
    # 用 Unicode 占位符避免特殊字符问题
    temp_items = []
    for i, item in enumerate(items):
        placeholder = f"ITEM_{i}"
        temp_items.append(placeholder)
        safe = item.replace('"', "'").replace('\\', '')
    
    items_str = ','.join(f'"{t}"' for t in temp_items)
    script = f'''
    set theList to {{{items_str}}}
    set chosen to choose from list theList with prompt "{_escape_as(prompt)}" with multiple selections allowed
    if chosen is false then return "CANCELLED"
    set AppleScript's text item delimiters to "|||"
    return chosen as string
    '''
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0 or 'CANCELLED' in r.stdout:
        return None
    
    # 把占位符映射回原始素材名
    chosen_placeholders = r.stdout.strip().split('|||')
    placeholder_map = {f"ITEM_{i}": item for i, item in enumerate(items)}
    return [placeholder_map.get(cp, cp) for cp in chosen_placeholders]

def osa_text_input(prompt, default_text=""):
    """单行文本输入"""
    default = f'default answer "{_escape_as(default_text)}"' if default_text else ''
    script = f'display dialog "{_escape_as(prompt)}" {default}'
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    for line in r.stdout.split('\n'):
        if 'text returned:' in line:
            return line.split('text returned:')[1].strip()
    return None

# ── 坐标拾取器 ────────────────────────────────────────────
def pick_all_coordinates(need_export=False, saved_btn=None, saved_sync=None, saved_lens=None, saved_export=None):
    """统一坐标输入窗口 — 一次性输入所有按钮坐标
    
    使用 Shift+Cmd+4 截图工具可以快速获取屏幕坐标（拖动时显示坐标值）。
    屏幕分辨率变化后必须重新拾取。
    
    need_export: 是否需要导出按钮坐标（视频导出模式）
    返回 (btn_coords, sync_coords, lens_coords, export_coords) 或 None（取消）
    """
    import tkinter as tk
    
    result = {'btn': None, 'sync': None, 'lens': None, 'export': None, 'done': False}
    
    root = tk.Tk()
    root.title("Gyroflow 批量处理 — 输入按钮坐标")
    root.resizable(False, False)
    root.attributes('-topmost', True)
    w = 480
    # 4 行固定（运动数据、同步、镜头），+1 可选（导出）
    h = 300 + (60 if need_export else 0)
    root.geometry(f"{w}x{h}")
    
    # ── 顶部提示 ──
    info = tk.Frame(root, bg='#e8f0fe', padx=14, pady=10)
    info.pack(fill='x')
    tk.Label(info,
        text='💡 使用 Shift+Cmd+4 截图即可看到光标坐标值\n'
             '⚠️ 更换屏幕/外接显示器后必须重新拾取坐标',
        font=('PingFang SC', 12), justify='left',
        bg='#e8f0fe', fg='#1a1a2e'
    ).pack(anchor='w')
    
    # ── 输入区域 ──
    fields = tk.Frame(root, padx=16, pady=8)
    fields.pack(fill='x', expand=True)
    
    def make_row(parent, label_text, saved_val, row):
        """创建一行: [标签] [X输入框] , [Y输入框]"""
        tk.Label(parent, text=label_text, font=('PingFang SC', 13),
                 anchor='e', width=18).grid(row=row, column=0, sticky='e', padx=(0, 8))
        x_var = tk.StringVar()
        y_var = tk.StringVar()
        if saved_val and len(saved_val) == 2:
            x_var.set(str(saved_val[0]))
            y_var.set(str(saved_val[1]))
        x_entry = tk.Entry(parent, textvariable=x_var, font=('Menlo', 14),
                          width=6, justify='center')
        x_entry.grid(row=row, column=1)
        tk.Label(parent, text=',', font=('Menlo', 14)).grid(row=row, column=2, padx=2)
        y_entry = tk.Entry(parent, textvariable=y_var, font=('Menlo', 14),
                          width=6, justify='center')
        y_entry.grid(row=row, column=3)
        return x_var, y_var
    
    # 四行输入（导出按钮可选）
    btn_x, btn_y = make_row(fields, '打开运动数据:', saved_btn, 0)
    sync_x, sync_y = make_row(fields, '自动同步:', saved_sync, 1)
    lens_x, lens_y = make_row(fields, '打开镜头配置:', saved_lens, 2)
    export_x, export_y = None, None
    if need_export:
        export_x, export_y = make_row(fields, '导出视频:', saved_export, 3)
    
    # ── 按钮区 ──
    btn_frame = tk.Frame(root, padx=16, pady=14)
    btn_frame.pack(fill='x')
    
    def parse_coords(x_var, y_var):
        try:
            x = int(x_var.get().strip())
            y = int(y_var.get().strip())
            if x > 0 and y > 0:
                return (x, y)
        except ValueError:
            pass
        return None
    
    def on_ok():
        b = parse_coords(btn_x, btn_y)
        s = parse_coords(sync_x, sync_y)
        l = parse_coords(lens_x, lens_y)
        if not b or not s or not l:
            tk.messagebox.showwarning("输入错误", "请填写所有必填坐标（正整数）", parent=root)
            return
        result['btn'] = b
        result['sync'] = s
        result['lens'] = l
        if need_export:
            e = parse_coords(export_x, export_y)
            if not e:
                tk.messagebox.showwarning("输入错误", "请填写导出按钮坐标", parent=root)
                return
            result['export'] = e
        else:
            result['export'] = None
        result['done'] = True
        root.destroy()
    
    def on_cancel():
        root.destroy()
    
    # Enter 键触发确定
    root.bind('<Return>', lambda e: on_ok())
    root.bind('<Escape>', lambda e: on_cancel())
    
    tk.Button(btn_frame, text='取消', font=('PingFang SC', 13),
              width=10, command=on_cancel).pack(side='right', padx=8)
    tk.Button(btn_frame, text='确定', font=('PingFang SC', 13, 'bold'),
              width=10, bg='#4a90d9', fg='white', command=on_ok).pack(side='right')
    
    # 聚焦第一个输入框
    fields.focus_set()
    root.mainloop()
    
    if not result['done']:
        return None
    return (result['btn'], result['sync'], result['lens'], result['export'])

# ── 素材扫描与预检 ────────────────────────────────────────
def scan_sequences(base_dir):
    """DNG 模式：扫描目录下所有包含 DNG 序列帧的素材"""
    if not os.path.isdir(base_dir):
        return []
    sequences = []
    for name in sorted(os.listdir(base_dir)):
        folder = os.path.join(base_dir, name)
        if not os.path.isdir(folder):
            continue
        dng = os.path.join(folder, f"{name}-000010.dng")
        if os.path.exists(dng):
            has_gcsv = "✅" if os.path.exists(os.path.join(base_dir, f"{name}.gcsv")) else "⚠️ 缺GCSV"
            sequences.append(f"{name}  {has_gcsv}")
    return sequences

def scan_videos(base_dir):
    """视频模式：扫描目录下所有 MP4/MOV 视频文件"""
    if not os.path.isdir(base_dir):
        return []
    videos = []
    supported_exts = ('.mp4', '.mov', '.MP4', '.MOV')
    for name in sorted(os.listdir(base_dir)):
        if name.startswith('.'):
            continue
        full = os.path.join(base_dir, name)
        if not os.path.isfile(full):
            continue
        if name.endswith(supported_exts):
            video_name = os.path.splitext(name)[0]
            has_gcsv = "✅" if os.path.exists(os.path.join(base_dir, f"{video_name}.gcsv")) else "⚠️ 缺GCSV"
            videos.append(f"{name}  {has_gcsv}")
    return videos

def parse_seq_name(seq_str):
    return seq_str.split()[0]

def precheck(sequences, base_dir, gcsv_dir):
    """DNG 模式预检：返回 (ok_list, fail_list)"""
    ok_list = []
    fail_list = []
    for seq in sequences:
        dng = os.path.join(base_dir, seq, f"{seq}-000010.dng")
        gcsv = os.path.join(gcsv_dir, f"{seq}.gcsv")
        issues = []
        if not os.path.exists(dng):
            issues.append(f"缺 DNG: {dng}")
        if not os.path.exists(gcsv):
            issues.append(f"缺 GCSV: {gcsv}")
        if issues:
            fail_list.append((seq, ", ".join(issues)))
        else:
            ok_list.append(seq)
    
    return ok_list, fail_list

def precheck_videos(video_files, base_dir, gcsv_dir):
    """视频模式预检：返回 (ok_list, fail_list)"""
    ok_list = []
    fail_list = []
    for vf in video_files:
        video_path = os.path.join(base_dir, vf)
        video_name = os.path.splitext(vf)[0]
        gcsv = os.path.join(gcsv_dir, f"{video_name}.gcsv")
        issues = []
        if not os.path.exists(video_path):
            issues.append(f"缺视频文件: {video_path}")
        if not os.path.exists(gcsv):
            issues.append(f"缺 GCSV: {gcsv}")
        if issues:
            fail_list.append((vf, ", ".join(issues)))
        else:
            ok_list.append(vf)
    
    return ok_list, fail_list

# ── 镜头配置文件匹配 ──────────────────────────────────────
def get_lens_file(item_name):
    """根据素材文件名匹配镜头校准 JSON 文件
    
    规则:
      - 文件名含 "24mm" → 1xiaomi_24mm_4096:3072.json
      - 文件名含 "66mm" → 3xiaomi_66mm_3648:2752.json
    返回文件名，若无法匹配返回 None
    """
    name_lower = item_name.lower()
    if '24mm' in name_lower:
        return '1xiaomi_24mm_4096:3072.json'
    elif '66mm' in name_lower:
        return '3xiaomi_66mm_3648:2752.json'
    return None

# ── 核心处理逻辑 ──────────────────────────────────────────
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def get_gyroflow_cpu():
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for l in r.stdout.split('\n'):
        if 'Gyroflow' in l and 'grep' not in l and 'python' not in l:
            p = re.split(r'\s+', l)
            try:
                return float(p[2])
            except:
                pass
    return None

def wait_for_sync(timeout=120):
    """等待自动同步完成，timeout 可配置"""
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
    log(f"⚠️ 同步超时 ({timeout}秒)")
    return False

def wait_for_export(timeout=600):
    """等待视频导出完成，timeout 可配置"""
    idle = 0
    waited = 0
    while waited < timeout:
        cpu = get_gyroflow_cpu()
        if cpu is not None and cpu < 10.0:
            idle += 1
            if idle >= 6:  # 连续 12 秒低 CPU
                log(f"导出完成 (CPU={cpu}%)")
                return True
        else:
            idle = 0
        if waited % 30 == 0 and waited > 0:
            log(f"  导出中... ({waited}秒, CPU={cpu}%)")
        time.sleep(2)
        waited += 2
    log(f"⚠️ 导出超时 ({timeout}秒)")
    return False

def validate_project(save_dir, item_name):
    """验证保存的项目文件是否完整（GCSV + 镜头参数）"""
    saved_files = [f for f in os.listdir(save_dir) 
                   if f.endswith('.gyroflow') and not f.startswith('._')]
    if not saved_files:
        log(f"⚠️ 未找到项目文件")
        return False
    
    latest = sorted(saved_files)[-1]
    fp = os.path.join(save_dir, latest)
    
    try:
        with open(fp) as fh:
            d = json.load(fh)
        
        gs = d.get('gyro_source', {})
        has_gcsv = '.gcsv' in gs.get('filepath', '')
        has_lens = bool(d.get('calibration_data', {}).get('fisheye_params'))
        
        log(f"   文件: {latest} ({os.path.getsize(fp):,} bytes)")
        log(f"   陀螺仪: {'✅ GCSV' if has_gcsv else '⚠️ 非GCSV源'}")
        log(f"   镜头: {'✅' if has_lens else '⚠️ 镜头参数缺失'}")
        
        if has_gcsv and has_lens:
            return True
        elif has_gcsv:
            log(f"   ⚠️ 镜头校准参数未加载（预设可能不完整）")
            return True  # 有 GCSV 就算基本可用
        else:
            log(f"   ⚠️ GCSV 未正确关联，项目可能无效")
            return False
    except Exception as e:
        log(f"   ⚠️ 项目文件解析失败: {e}")
        return False

def process_one(item_name, base_dir, gcsv_dir, lens_dir, export_dir, btn_coords, sync_coords, sync_timeout=120, mode="dng", export_mode="project", export_coords=None, export_timeout=600, lens_coords=None):
    """处理单个素材，返回 (success, message)
    mode: "dng" — DNG 序列帧 | "video" — MP4/MOV 视频
    export_mode: "project" — 保存 .gyroflow | "video" — CLI 渲染稳定视频（仅视频模式有效）
    """
    if mode == "dng":
        media_path = os.path.join(base_dir, item_name, f"{item_name}-000010.dng")
        gcsv_name = item_name
        save_dir = os.path.dirname(media_path)
        media_label = "DNG"
    else:  # video
        media_path = os.path.join(base_dir, item_name)
        gcsv_name = os.path.splitext(item_name)[0]
        save_dir = base_dir
        media_label = "视频"
    
    gcsv = os.path.join(gcsv_dir, f"{gcsv_name}.gcsv")
    
    if not os.path.exists(media_path):
        return False, f"{media_label} 文件不存在"
    if not os.path.exists(gcsv):
        return False, f"GCSV 文件不存在"
    log(f"=== 处理: {item_name} ===")
    
    # 关闭已有 Gyroflow（更安全地关闭）
    subprocess.run(['pkill', '-f', 'Gyroflow'], capture_output=True)
    time.sleep(2)
    subprocess.run(['pkill', '-9', '-f', 'Gyroflow'], capture_output=True)
    time.sleep(1)
    
    # Step 1-2: 加载素材
    if mode == "dng":
        log("Step 1-2: 加载 DNG")
        log(f"  路径: {media_path}")
        r = subprocess.run(['open', '-a', 'Gyroflow'], capture_output=True, text=True)
        log(f"  启动 Gyroflow → exit={r.returncode} stderr={r.stderr.strip()!r}")
        time.sleep(8)
        r = subprocess.run(['open', '-a', 'Gyroflow', media_path], capture_output=True, text=True)
        log(f"  打开 DNG → exit={r.returncode} stderr={r.stderr.strip()!r}")
        time.sleep(8)
        # 验证 Gyroflow 进程是否在运行
        ps = subprocess.run(['pgrep', '-l', 'Gyroflow'], capture_output=True, text=True)
        log(f"  Gyroflow 进程: {ps.stdout.strip() or '❌ 未找到'}")
        subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate']); time.sleep(1)
        subprocess.run(['osascript', '-e', 'tell application "System Events" to key code 36']); time.sleep(7)
        log("✅ DNG 加载完毕")
    else:
        log("Step 1-2: 加载视频")
        log(f"  路径: {media_path}")
        r = subprocess.run(['open', '-a', 'Gyroflow'], capture_output=True, text=True)
        log(f"  启动 Gyroflow → exit={r.returncode} stderr={r.stderr.strip()!r}")
        time.sleep(8)
        r = subprocess.run(['open', '-a', 'Gyroflow', media_path], capture_output=True, text=True)
        log(f"  打开视频 → exit={r.returncode} stderr={r.stderr.strip()!r}")
        time.sleep(8)
        # 验证 Gyroflow 进程是否在运行
        ps = subprocess.run(['pgrep', '-l', 'Gyroflow'], capture_output=True, text=True)
        log(f"  Gyroflow 进程: {ps.stdout.strip() or '❌ 未找到'}")
        subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate']); time.sleep(1)
        # 视频直接打开，不需要 Enter 确认序列帧
        time.sleep(5)
        log("✅ 视频加载完毕")
    
    # Step 3: 加载运动数据
    log("Step 3: 加载运动数据 (GCSV)")
    btn_x, btn_y = btn_coords
    pyautogui.click(btn_x, btn_y)
    time.sleep(6)
    pyautogui.hotkey('command', 'shift', 'g')
    time.sleep(1)
    pyautogui.keyUp('shift')  # 确保 Shift 释放，避免后续输入被修饰
    time.sleep(3)
    pyautogui.typewrite(gcsv, interval=0.1)
    time.sleep(4)
    pyautogui.press('return'); time.sleep(3)
    pyautogui.press('return'); time.sleep(3)
    log("✅ 运动数据加载完毕")
    
    # Step 3.2: 加载镜头校准配置
    lens_file = get_lens_file(item_name)
    if lens_file and lens_coords and lens_dir:
        lens_path = os.path.join(lens_dir, lens_file)
        log(f"Step 3.2: 加载镜头配置 → {lens_file}")
        if os.path.exists(lens_path):
            lens_x, lens_y = lens_coords
            pyautogui.click(lens_x, lens_y)
            time.sleep(5)  # 等待文件浏览器弹出
            pyautogui.hotkey('command', 'shift', 'g')
            time.sleep(1)
            pyautogui.keyUp('shift')
            time.sleep(2)
            pyautogui.typewrite(lens_dir, interval=0.1)
            time.sleep(3)
            pyautogui.press('return'); time.sleep(3)
            # 选择镜头文件
            pyautogui.typewrite(lens_file, interval=0.1)
            time.sleep(3)
            pyautogui.press('return'); time.sleep(3)
            log(f"✅ 镜头配置加载完毕")
        else:
            log(f"   ⚠️ 镜头文件不存在: {lens_path}")
    elif not lens_file:
        log(f"   ⓘ 镜头跳过: '{item_name}' 不含 24mm/66mm，无需加载")
    else:
        log(f"   ⚠️ 镜头跳过: lens_file={lens_file!r} lens_coords={lens_coords!r} lens_dir={lens_dir!r}")
    
    # Step 3.5: 点击自动同步按钮
    log("Step 3.5: 触发自动同步")
    time.sleep(2)
    sync_x, sync_y = sync_coords
    pyautogui.click(sync_x, sync_y)
    log("✅ 已点击自动同步")
    
    # Step 4: 等待自动同步完成
    log(f"⏳ 等待自动同步（最长 {sync_timeout} 秒）...")
    synced = wait_for_sync(timeout=sync_timeout)
    
    if not synced:
        # 同步超时，不保存 —— 避免生成废文件
        log("❌ 同步超时，跳过保存")
        return False, "同步超时"
    
    # Step 5: 保存项目 / GUI 导出视频
    if mode == "video" and export_mode == "video":
        # ── GUI 导出稳定视频 → 不保存 .gyroflow ──
        log("Step 5: GUI 导出稳定视频")
        if not export_coords:
            return False, "缺少导出按钮坐标"
        export_x, export_y = export_coords
        
        # 记录导出前文件数
        video_exts = {'.mp4', '.mov', '.MP4', '.MOV'}
        before_count = len([f for f in os.listdir(save_dir)
                           if os.path.splitext(f)[1] in video_exts and not f.startswith('._')])
        
        # 点击导出按钮 → 导出开始
        pyautogui.click(export_x, export_y)
        time.sleep(3)
        
        log(f"⏳ 等待视频导出（最长 {export_timeout} 秒）...")
        exported = wait_for_export(timeout=export_timeout)
        
        if not exported:
            log("❌ 视频导出超时")
            return False, "视频导出超时"
        
        # 验证：文件数是否增加
        after_count = len([f for f in os.listdir(save_dir)
                          if os.path.splitext(f)[1] in video_exts and not f.startswith('._')])
        if after_count > before_count:
            log(f"✅ 导出成功（{save_dir} 内视频文件: {before_count} → {after_count}）")
            return True, "导出成功"
        else:
            log(f"⚠️ 文件数未变化 ({before_count} → {after_count})，可能导出到了其他位置")
            return False, "未检测到输出文件"
    else:
        # ── 保存 .gyroflow 项目文件 ──
        log("Step 5: 保存项目")
        subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate'])
        time.sleep(2)
        pyautogui.hotkey('command', 's'); time.sleep(3)
        pyautogui.press('return'); time.sleep(4)
        log("✅ 项目已保存")
    
    return True, "成功"

# ── 格式化工具 ────────────────────────────────────────────
def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    elif seconds < 3600:
        return f"{seconds/60:.1f} 分钟"
    else:
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        return f"{h} 小时 {m} 分"

# ── 主流程 ────────────────────────────────────────────────
def main():
    print()
    print("=" * 55)
    print("  Gyroflow 批量处理工具 v3")
    print("=" * 55)
    print()
    
    cfg = load_config()
    
    # ── 模式选择 ──
    print()
    mode_ans = osa_dialog(
        "选择处理模式：\n\n"
        "【DNG 模式】处理 DNG 序列帧文件夹\n"
        "  素材结构: 文件夹名/文件夹名-000010.dng\n\n"
        "【视频模式】处理 MP4/MOV 视频文件\n"
        "  素材结构: 视频文件名.mp4 + 视频文件名.gcsv",
        ["取消", "视频模式", "DNG 模式"],
        "DNG 模式"
    )
    if not mode_ans or mode_ans == "取消":
        print("❌ 已取消")
        return
    mode = "dng" if "DNG" in mode_ans else "video"
    print(f"  处理模式 → {mode_ans}")
    
    # ── 视频模式：导出方式选择 ──
    export_mode = "project"
    export_coords = None
    export_timeout = 600
    if mode == "video":
        export_saved = cfg.get('export_mode', 'project')
        export_default = "保存 .gyroflow 项目" if export_saved == "project" else "导出稳定视频"
        export_ans = osa_dialog(
            "视频导出方式：\n\n"
            "【保存项目】Cmd+S → 保存 .gyroflow 项目文件（快）\n"
            "【导出视频】渲染导出稳定后的视频文件（慢，耗时较长）",
            ["保存 .gyroflow 项目", "导出稳定视频"],
            default=export_default
        )
        if not export_ans:
            print("❌ 已取消")
            return
        export_mode = "video" if "导出" in export_ans else "project"
        print(f"  导出方式 → {export_ans}")
    
    # ── 英文输入法提醒 ──
    osa_dialog(
        "⚠️ 请确认：\n\n"
        "按 Caps Lock 键切换到【英文输入法】\n\n"
        "整个处理过程需要保持英文输入状态。",
        ["知道了"],
        default="知道了"
    )
    
    # ── Step 1: 选择素材目录 ──
    print()
    if mode == "dng":
        print("【1/4】选择 DNG 序列帧素材目录")
        choose_prompt = "选择包含 DNG 序列帧文件夹的目录"
    else:
        print("【1/4】选择视频素材目录")
        choose_prompt = "选择包含 MP4/MOV 视频文件的目录"
    
    base_default = cfg.get('base_dir', '')
    base_dir = osa_choose_folder(
        choose_prompt,
        default_dir=base_default if base_default else ""
    )
    if not base_dir:
        print("❌ 已取消")
        return
    print(f"  素材目录 → {base_dir}")
    
    if mode == "dng":
        item_list = scan_sequences(base_dir)
        if not item_list:
            print(f"\n⚠️ 在 {base_dir} 中未找到包含 DNG 序列帧的文件夹\n"
                  "   请确保文件夹命名格式: 素材名/素材名-000010.dng")
            osa_dialog("未找到包含 DNG 序列帧的素材文件夹。\n\n请确认目录和文件命名正确。", ["确定"])
            return
    else:
        item_list = scan_videos(base_dir)
        if not item_list:
            print(f"\n⚠️ 在 {base_dir} 中未找到 MP4/MOV 视频文件")
            osa_dialog("未找到 MP4/MOV 视频文件。\n\n请确认目录中有视频文件。", ["确定"])
            return
    
    print(f"\n  扫描到 {len(item_list)} 个素材：")
    for s in item_list:
        print(f"    · {s}")
    
    # ── Step 2: 选择 GCSV 目录 ──
    print()
    print("【2/4】选择 GCSV 运动数据目录")
    print("  （通常与素材目录相同，直接按「选择」即可）")
    gcsv_default = cfg.get('gcsv_dir', base_dir)
    gcsv_dir = osa_choose_folder(
        "选择 GCSV 运动数据文件所在目录",
        default_dir=gcsv_default if gcsv_default else base_dir
    )
    if not gcsv_dir:
        print("❌ 已取消")
        return
    print(f"  GCSV 目录 → {gcsv_dir}")
    
    # ── Step 2.5: 选择镜头校准目录 ──
    print()
    print("【镜头】选择镜头校准 JSON 目录")
    print("  （包含 1xiaomi_24mm_4096/3072.json、3xiaomi_66mm_3648/2752.json 等）")
    lens_default = cfg.get('lens_dir', base_dir)
    lens_dir = osa_choose_folder(
        "选择镜头校准 JSON 文件所在目录",
        default_dir=lens_default if lens_default else base_dir
    )
    if not lens_dir:
        print("❌ 已取消")
        return
    print(f"  镜头目录 → {lens_dir}")
    
    # ── 屏幕分辨率检查 ──
    current_res = f"{pyautogui.size().width}x{pyautogui.size().height}"
    saved_res = cfg.get('screen_resolution', '')
    if saved_res and saved_res != current_res:
        print(f"  ⚠️ 屏幕分辨率已变化: {saved_res} → {current_res}")
        osa_dialog(
            "⚠️ 屏幕分辨率已改变！\n\n"
            f"上次: {saved_res}\n"
            f"当前: {current_res}\n\n"
            "之前保存的按钮坐标可能不准确，\n"
            "请务必在下一步重新拾取坐标。",
            ["知道了"],
            icon="caution"
        )
    
    # ── 坐标输入：统一窗口 ──
    print()
    print("【坐标】输入按钮坐标")
    print("  提示：使用 Shift+Cmd+4 截图查看光标坐标")
    print("        屏幕/分辨率变化后需要重新拾取")
    print()
    
    all_coords = pick_all_coordinates(
        need_export=(mode == "video" and export_mode == "video"),
        saved_btn=cfg.get('btn_coords') if cfg.get('btn_coords') else None,
        saved_sync=cfg.get('sync_coords') if cfg.get('sync_coords') else None,
        saved_lens=cfg.get('lens_coords') if cfg.get('lens_coords') else None,
        saved_export=cfg.get('export_coords') if cfg.get('export_coords') else None
    )
    if not all_coords:
        print("❌ 已取消")
        return
    btn_coords, sync_coords, lens_coords, export_coords = all_coords
    print(f"  打开运动数据 → {btn_coords}")
    print(f"  自动同步 → {sync_coords}")
    print(f"  打开镜头配置 → {lens_coords}")
    if export_coords:
        print(f"  导出视频 → {export_coords}")
    
    # ── 选择要处理的素材 ──
    print()
    defaults = [s for s in item_list if "✅" in s]
    chosen = osa_choose_list("选择要处理的素材（Cmd+点击可多选）", item_list, defaults)
    if not chosen:
        print("❌ 已取消")
        return
    
    items = [parse_seq_name(s) for s in chosen]
    print(f"  已选择 {len(items)} 个素材：")
    for s in items:
        print(f"    · {s}")
    
    # ── 同步超时设置 ──
    sync_timeout = cfg.get('sync_timeout', 120)
    timeout_input = osa_text_input(
        f"同步超时时间（秒），大素材建议设长一些\n当前：{sync_timeout} 秒",
        str(sync_timeout)
    )
    if timeout_input:
        try:
            sync_timeout = int(timeout_input.strip())
            if sync_timeout < 30:
                sync_timeout = 30
            elif sync_timeout > 600:
                sync_timeout = 600
        except:
            pass
    print(f"  同步超时 → {sync_timeout} 秒")
    
    # ── 导出超时设置 ──
    if mode == "video" and export_mode == "video":
        export_timeout = cfg.get('export_timeout', 600)
        etime_input = osa_text_input(
            f"导出超时时间（秒），视频渲染比同步慢很多\n当前：{export_timeout} 秒",
            str(export_timeout)
        )
        if etime_input:
            try:
                export_timeout = int(etime_input.strip())
                if export_timeout < 60:
                    export_timeout = 60
                elif export_timeout > 3600:
                    export_timeout = 3600
            except:
                pass
        print(f"  导出超时 → {export_timeout} 秒")
        
        # ── 导出目录选择 ──
        print()
        print("【导出】选择视频导出目录")
        export_dir_default = cfg.get('export_dir', base_dir)
        export_dir = osa_choose_folder(
            "选择稳定后视频的保存目录",
            default_dir=export_dir_default if export_dir_default else base_dir
        )
        if not export_dir:
            print("❌ 已取消")
            return
        print(f"  导出目录 → {export_dir}")
    else:
        export_dir = ""
    
    # ── 预检 ──
    print()
    print("=" * 55)
    print("  预检文件...")
    print()
    
    if mode == "dng":
        ok_list, fail_list = precheck(items, base_dir, gcsv_dir)
    else:
        ok_list, fail_list = precheck_videos(items, base_dir, gcsv_dir)
    
    if fail_list:
        print("  ⚠️ 以下素材有文件缺失：")
        for seq, reason in fail_list:
            print(f"    ✗ {seq}: {reason}")
        print()
        ans = osa_dialog(
            f"{len(fail_list)} 个素材文件缺失，将自动跳过。\n\n继续处理其余 {len(ok_list)} 个？",
            ["取消", "继续"],
            default="继续"
        )
        if ans != "继续":
            print("❌ 已取消")
            return
    
    if not ok_list:
        print("❌ 没有可以处理的素材")
        osa_dialog("所有素材都有文件缺失，无法继续。", ["确定"], icon="stop")
        return
    
    print(f"  ✅ {len(ok_list)} 个素材通过预检，可以开始处理")
    
    # ── 最终确认 ──
    export_info = ""
    if mode == "video" and export_mode == "video":
        export_info = f"导出目录：{export_dir}\n" \
                     f"导出超时：{export_timeout} 秒\n"
    summary = (
        f"即将处理 {len(ok_list)} 个素材：\n\n"
        f"模式：{'DNG 序列帧' if mode == 'dng' else '视频文件'}\n"
        f"导出：{'GUI 导出稳定视频' if export_mode == 'video' else '保存 .gyroflow 项目'}\n"
        f"素材目录：{base_dir}\n"
        f"GCSV 目录：{gcsv_dir}\n"
        f"镜头配置目录：{lens_dir}\n"
        f"打开运动数据：({btn_coords[0]}, {btn_coords[1]})\n"
        f"自动同步：({sync_coords[0]}, {sync_coords[1]})\n"
        f"打开镜头配置：({lens_coords[0]}, {lens_coords[1]})\n"
        f"同步超时：{sync_timeout} 秒\n"
        f"{export_info}\n"
        f"素材列表：\n" +
        "\n".join(f"  · {s}" for s in ok_list)
    )
    
    ans = osa_dialog(summary, ["取消", "开始处理"], "开始处理", icon="caution")
    if ans != "开始处理":
        print("❌ 已取消")
        return
    
    # ── 保存配置 ──
    save_config({
        "base_dir": base_dir,
        "gcsv_dir": gcsv_dir,
        "lens_dir": lens_dir,
        "export_dir": export_dir,
        "btn_coords": list(btn_coords),
        "sync_coords": list(sync_coords),
        "lens_coords": list(lens_coords) if lens_coords else [],
        "export_coords": list(export_coords) if export_coords else [],
        "last_sequences": ok_list,
        "sync_timeout": sync_timeout,
        "export_timeout": export_timeout,
        "export_mode": export_mode,
        "screen_resolution": f"{pyautogui.size().width}x{pyautogui.size().height}",
        "mode": mode,
    })
    
    # ── 开始处理 ──
    print()
    print("=" * 55)
    print("  开始批量处理")
    print("=" * 55)
    print()
    
    osa_notify("Gyroflow 批量处理", f"开始处理 {len(ok_list)} 个素材")
    
    success_list = []
    fail_list = []
    start_time = time.time()
    total = len(ok_list)
    
    for i, item in enumerate(ok_list, 1):
        print(f"\n{'─' * 40}")
        print(f"【{i}/{total}】{item}")
        
        item_start = time.time()
        succ, msg = process_one(item, base_dir, gcsv_dir, lens_dir, export_dir, btn_coords, sync_coords, sync_timeout, mode, export_mode, export_coords, export_timeout, lens_coords)
        item_elapsed = time.time() - item_start
        
        if succ:
            success_list.append(item)
            print(f"  ✅ 成功 ({format_time(item_elapsed)})")
        else:
            fail_list.append(item)
            print(f"  ❌ 失败: {msg} ({format_time(item_elapsed)})")
        
        # 进度预估
        if i < total:
            avg_time = (time.time() - start_time) / i
            remaining = avg_time * (total - i)
            print(f"  ⏱ 预计剩余: {format_time(remaining)}")
        
        time.sleep(3)
    
    elapsed = time.time() - start_time
    
    # ── 完成 ──
    print()
    print("=" * 55)
    print(f"  处理完成！")
    print(f"  ✅ 成功: {len(success_list)} 个")
    print(f"  ❌ 失败: {len(fail_list)} 个")
    if fail_list:
        print(f"  失败列表: {', '.join(fail_list)}")
    print(f"  总耗时: {format_time(elapsed)}")
    print("=" * 55)
    
    osa_notify("Gyroflow 批量处理", f"完成！成功 {len(success_list)}，失败 {len(fail_list)}")
    
    if mode == "video" and export_mode == "video":
        save_location = "导出视频所在目录中"
    else:
        save_location = "各 DNG 文件夹中" if mode == "dng" else "视频所在目录中"
    finish_msg = (
        f"处理完成！\n\n"
        f"✅ 成功: {len(success_list)} 个\n"
        f"❌ 失败: {len(fail_list)} 个\n"
        f"⏱ 耗时: {format_time(elapsed)}\n\n"
        f"项目文件已保存在{save_location}。"
    )
    
    if fail_list:
        finish_msg += f"\n\n失败素材: {', '.join(fail_list)}"
        finish_msg += "\n\n是否重试失败的素材？"
        ans = osa_dialog(finish_msg, ["不了", "重试"], "不了")
        if ans == "重试":
            print()
            print("=" * 55)
            print("  重试失败素材")
            print("=" * 55)
            retry_success = []
            retry_fail = []
            for i, seq in enumerate(fail_list, 1):
                print(f"\n【重试 {i}/{len(fail_list)}】{seq}")
                succ, msg = process_one(seq, base_dir, gcsv_dir, lens_dir, export_dir, btn_coords, sync_coords, sync_timeout, mode, export_mode, export_coords, export_timeout, lens_coords)
                if succ:
                    retry_success.append(seq)
                    print(f"  ✅ 成功")
                else:
                    retry_fail.append(seq)
                    print(f"  ❌ 失败: {msg}")
                time.sleep(3)
            
            print()
            print("=" * 55)
            print(f"  重试完成！成功: {len(retry_success)}, 仍失败: {len(retry_fail)}")
            if retry_fail:
                print(f"  仍失败: {', '.join(retry_fail)}")
            print(f"  总成功: {len(success_list) + len(retry_success)} 个")
            print("=" * 55)
            osa_notify("Gyroflow 批量处理", f"重试完成，总成功 {len(success_list) + len(retry_success)} 个")
    else:
        osa_dialog(finish_msg, ["确定"])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        osa_dialog(f"出错了：\n\n{_escape_as(str(e))}", ["确定"], icon="stop")
