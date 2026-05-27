#!/usr/bin/env python3
"""
Gyroflow 批量处理工具 — GUI 版 v2
双击打开，原生 macOS 对话框选择文件，无需手动改代码。

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
import pyautogui, subprocess, time, os, re, json, sys, tempfile, shlex

pyautogui.FAILSAFE = False

# ── 配置保存 ──────────────────────────────────────────────
CONFIG_FILE = os.path.expanduser("~/.gyroflow_batch_config.json")

DEFAULT_CONFIG = {
    "base_dir": "",
    "gcsv_dir": "",
    "btn_coords": [],
    "sync_coords": [],
    "last_sequences": [],
    "sync_timeout": 120,
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
def pick_coordinates(default_coords=None):
    """拾取按钮坐标：可视化点击 或 手动输入"""
    default_str = ""
    if default_coords:
        default_str = f"{default_coords[0]},{default_coords[1]}"
    
    # 合并为单个对话框（原来的两个合并为一个）
    ans = osa_dialog(
        "如何获取按钮坐标？\n\n"
        "【可视化拾取】屏幕变暗 → 点按钮 → 自动记录\n"
        "【手动输入】直接输入坐标值（如 128,697）\n\n"
        "\u26a0\ufe0f 换了电脑/屏幕请务必重新拾取！",
        ["取消", "手动输入", "可视化拾取"],
        "可视化拾取"
    )
    
    if ans == "可视化拾取":
        # 写临时脚本（中文提示，避免 emoji/shell 问题）
        picker_code = '''import tkinter as tk, sys
class Picker:
    def __init__(self):
        self.r = tk.Tk()
        self.r.title("Pick Coords")
        self.r.attributes('-fullscreen', True)
        self.r.attributes('-alpha', 0.38)
        self.r.attributes('-topmost', True)
        self.r.configure(bg='black', cursor='crosshair')
        f = tk.Frame(self.r, bg='black')
        f.pack(expand=True)
        tk.Label(f, text='\u8bf7\u70b9\u51fb Gyroflow \u7a97\u53e3\u4e2d',
                 font=('PingFang SC', 26, 'bold'), fg='white', bg='black',
                 justify='center').pack(pady=(0, 3))
        tk.Label(f, text='\u300c\u8fd0\u52a8\u6570\u636e\u300d\u4e0b\u65b9\u7684\u300c\u6253\u5f00\u6587\u4ef6\u300d\u6309\u94ae',
                 font=('PingFang SC', 26, 'bold'), fg='white', bg='black',
                 justify='center').pack(pady=(0, 12))
        tk.Label(f, text='\u6309 ESC \u53d6\u6d88',
                 font=('PingFang SC', 15), fg='#666', bg='black').pack()
        self.r.bind('<Button-1>', self.click)
        self.r.bind('<Escape>', lambda e: sys.exit(1))
    def click(self, e):
        cv = tk.Canvas(self.r, width=60, height=60, bg='black', highlightthickness=0)
        cv.place(x=e.x_root-30, y=e.y_root-30)
        cv.create_line(15, 30, 45, 30, fill='red', width=3)
        cv.create_line(30, 15, 30, 45, fill='red', width=3)
        cv.create_oval(5, 5, 55, 55, outline='red', width=2)
        self.r.after(500, lambda: self.done(e.x_root, e.y_root))
    def done(self, x, y):
        print(f"{x},{y}")
        self.r.destroy()
Picker().r.mainloop()'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(picker_code)
            tmp_path = f.name
        r = subprocess.run(['/usr/bin/python3', tmp_path], capture_output=True, text=True)
        os.unlink(tmp_path)
        if r.returncode != 0:
            print(f"坐标拾取出错: {r.stderr}")
            return None
        try:
            x, y = r.stdout.strip().split(',')
            return (int(x), int(y))
        except:
            return None
    
    elif ans == "手动输入":
        result = osa_text_input("请输入按钮坐标（格式: x,y）", default_str)
        if not result:
            return None
        try:
            x, y = result.replace(' ', '').split(',')
            return (int(x), int(y))
        except:
            osa_dialog("坐标格式错误，请使用 x,y 格式（如 128,697）", ["确定"], icon="stop")
            return None
    
    return None

# ── 素材扫描与预检 ────────────────────────────────────────
def scan_sequences(base_dir):
    """扫描目录下所有包含 DNG 序列帧的素材"""
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

def parse_seq_name(seq_str):
    return seq_str.split()[0]

def precheck(sequences, base_dir, gcsv_dir):
    """预检所有文件，返回 (ok_list, fail_list)"""
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

def validate_project(dng_dir, seq_name):
    """验证保存的项目文件是否完整（GCSV + 镜头参数）"""
    saved_files = [f for f in os.listdir(dng_dir) 
                   if f.endswith('.gyroflow') and not f.startswith('._')]
    if not saved_files:
        log(f"⚠️ 未找到项目文件")
        return False
    
    latest = sorted(saved_files)[-1]
    fp = os.path.join(dng_dir, latest)
    
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

def process_one(seq_name, base_dir, gcsv_dir, btn_coords, sync_coords, sync_timeout=120):
    """处理单个素材，返回 (success, message)"""
    dng = os.path.join(base_dir, seq_name, f"{seq_name}-000010.dng")
    gcsv = os.path.join(gcsv_dir, f"{seq_name}.gcsv")
    dng_dir = os.path.dirname(dng)
    
    if not os.path.exists(dng):
        return False, f"DNG 文件不存在"
    if not os.path.exists(gcsv):
        return False, f"GCSV 文件不存在"
    log(f"=== 处理: {seq_name} ===")
    
    # 关闭已有 Gyroflow（更安全地关闭）
    subprocess.run(['pkill', '-f', 'Gyroflow'], capture_output=True)
    time.sleep(2)
    subprocess.run(['pkill', '-9', '-f', 'Gyroflow'], capture_output=True)
    time.sleep(1)
    
    # Step 1-2: 加载 DNG
    log("Step 1-2: 加载 DNG")
    subprocess.run(['open', '-a', 'Gyroflow']); time.sleep(8)
    subprocess.run(['open', '-a', 'Gyroflow', dng]); time.sleep(8)
    subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate']); time.sleep(1)
    subprocess.run(['osascript', '-e', 'tell application "System Events" to key code 36']); time.sleep(7)
    log("✅ DNG 加载完毕")
    
    # Step 3: 加载运动数据
    log("Step 3: 加载运动数据 (GCSV)")
    btn_x, btn_y = btn_coords
    pyautogui.click(btn_x, btn_y)
    time.sleep(6)
    pyautogui.hotkey('command', 'shift', 'g')
    time.sleep(4)
    pyautogui.typewrite(gcsv, interval=0.1)
    time.sleep(4)
    pyautogui.press('return'); time.sleep(3)
    pyautogui.press('return'); time.sleep(6)
    log("✅ 运动数据加载完毕")
    
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
    
    # Step 5: 保存项目
    log("Step 5: 保存项目")
    subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate'])
    time.sleep(2)
    pyautogui.hotkey('command', 's'); time.sleep(4)
    pyautogui.press('return'); time.sleep(4)
    log("✅ 项目已保存")
    
    # Step 6: 验证
    log("Step 6: 验证项目")
    valid = validate_project(dng_dir, seq_name)
    
    if valid:
        return True, "成功"
    else:
        return False, "验证未通过"

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
    print("  Gyroflow 批量处理工具 v2")
    print("=" * 55)
    print()
    
    cfg = load_config()
    
    # ── 英文输入法提醒 ──
    osa_dialog(
        "⚠️ 请确认：\n\n"
        "按 Caps Lock 键切换到【英文输入法】\n\n"
        "整个处理过程需要保持英文输入状态。",
        ["知道了"]
    )
    
    # ── Step 1: 选择 DNG 素材目录 ──
    print()
    print("【1/4】选择 DNG 序列帧素材目录")
    base_default = cfg.get('base_dir', '')
    base_dir = osa_choose_folder(
        "选择包含 DNG 序列帧文件夹的目录",
        default_dir=base_default if base_default else ""
    )
    if not base_dir:
        print("❌ 已取消")
        return
    print(f"  素材目录 → {base_dir}")
    
    seq_list = scan_sequences(base_dir)
    if not seq_list:
        print(f"\n⚠️ 在 {base_dir} 中未找到包含 DNG 序列帧的文件夹\n"
              "   请确保文件夹命名格式: 素材名/素材名-000010.dng")
        osa_dialog("未找到包含 DNG 序列帧的素材文件夹。\n\n请确认目录和文件命名正确。", ["确定"])
        return
    
    print(f"\n  扫描到 {len(seq_list)} 个素材：")
    for s in seq_list:
        print(f"    · {s}")
    
    # ── Step C: 选择 GCSV 目录 ──
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
    
    # ── Step 3: 拾取打开文件按钮坐标 ──
    print()
    print("【3/4】拾取「打开文件」按钮坐标")
    print("  提示：请先打开 Gyroflow，")
    print("        界面左下角「运动数据」下方就是「打开文件」按钮")
    print()
    
    btn_coords = pick_coordinates(
        default_coords=tuple(cfg.get('btn_coords', [])) if cfg.get('btn_coords') else None
    )
    if not btn_coords:
        print("❌ 坐标拾取失败或已取消")
        return
    print(f"  打开文件按钮 → {btn_coords}")
    
    # ── 拾取自动同步按钮坐标 ──
    print()
    print("【4/4】拾取「自动同步」按钮坐标")
    print("  提示：在 Gyroflow 界面中找到「自动同步」按钮，")
    print("        通常位于运动数据区域附近")
    print()
    
    sync_coords = pick_coordinates(
        default_coords=tuple(cfg.get('sync_coords', [])) if cfg.get('sync_coords') else None
    )
    if not sync_coords:
        print("❌ 坐标拾取失败或已取消")
        return
    print(f"  自动同步按钮 → {sync_coords}")
    
    # ── 选择要处理的素材 ──
    print()
    defaults = [s for s in seq_list if "✅" in s]
    chosen = osa_choose_list("选择要处理的素材（Cmd+点击可多选）", seq_list, defaults)
    if not chosen:
        print("❌ 已取消")
        return
    
    sequences = [parse_seq_name(s) for s in chosen]
    print(f"  已选择 {len(sequences)} 个素材：")
    for s in sequences:
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
    
    # ── 预检 ──
    print()
    print("=" * 55)
    print("  预检文件...")
    print()
    
    ok_list, fail_list = precheck(sequences, base_dir, gcsv_dir)
    
    if fail_list:
        print("  ⚠️ 以下素材有文件缺失：")
        for seq, reason in fail_list:
            print(f"    ✗ {seq}: {reason}")
        print()
        ans = osa_dialog(
            f"{len(fail_list)} 个素材文件缺失，将自动跳过。\n\n继续处理其余 {len(ok_list)} 个？",
            ["取消", "继续"]
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
    summary = (
        f"即将处理 {len(ok_list)} 个素材：\n\n"
        f"素材目录：{base_dir}\n"
        f"GCSV 目录：{gcsv_dir}\n"
        f"打开文件按钮：({btn_coords[0]}, {btn_coords[1]})\n"
        f"自动同步按钮：({sync_coords[0]}, {sync_coords[1]})\n"
        f"同步超时：{sync_timeout} 秒\n\n"
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
        "btn_coords": list(btn_coords),
        "sync_coords": list(sync_coords),
        "last_sequences": ok_list,
        "sync_timeout": sync_timeout,
        "screen_resolution": f"{pyautogui.size().width}x{pyautogui.size().height}",
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
    
    for i, seq in enumerate(ok_list, 1):
        print(f"\n{'─' * 40}")
        print(f"【{i}/{total}】{seq}")
        
        item_start = time.time()
        succ, msg = process_one(seq, base_dir, gcsv_dir, btn_coords, sync_coords, sync_timeout)
        item_elapsed = time.time() - item_start
        
        if succ:
            success_list.append(seq)
            print(f"  ✅ 成功 ({format_time(item_elapsed)})")
        else:
            fail_list.append(seq)
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
    
    finish_msg = (
        f"处理完成！\n\n"
        f"✅ 成功: {len(success_list)} 个\n"
        f"❌ 失败: {len(fail_list)} 个\n"
        f"⏱ 耗时: {format_time(elapsed)}\n\n"
        f"项目文件已保存在各 DNG 文件夹中。"
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
                succ, msg = process_one(seq, base_dir, gcsv_dir, btn_coords, sync_coords, sync_timeout)
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
