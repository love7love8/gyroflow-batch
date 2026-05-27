# Gyroflow DNG 序列帧批量自动化处理流程

> 适用环境：macOS + Gyroflow v1.6.3  
> 工具：pyautogui、pyperclip（Python 库）  
> 最后更新：2026-05-19

---

## 一、前置准备

### 1. 安装依赖
```bash
pip3 install pyautogui pyperclip
```

### 2. 辅助功能权限
- **系统设置 → 隐私与安全性 → 辅助功能** → 允许 Terminal（或 Python）
- **系统设置 → 隐私与安全性 → 屏幕录制** → 允许 Terminal（或 Python）

### 3. 输入法
- 运行前请按 **Caps Lock** 切换到**英文输入法**
- 整个流程中保持英文输入法

### 4. 文件命名规则
- DNG 序列：`序列名-000010.dng`、`序列名-000011.dng`……
- GCSV 文件：`序列名.gcsv`（放在 DNG 文件夹的**上一级**）
- 预设文件：`.gyroflow` 预设文件（包含镜头校准 + 稳定参数 + 自动同步设置）

---

## 二、单个素材的处理流程

### Step 1-2：加载 DNG 序列帧
```bash
open -a Gyroflow
sleep 7
open -a Gyroflow "/path/to/序列名-000010.dng"
sleep 7
osascript -e 'tell application "gyroflow" to activate'
sleep 0.5
osascript -e 'tell application "System Events" to key code 36'
sleep 6
```
**说明：**
- `open -a Gyroflow` 先打开 Gyroflow 应用
- `open -a Gyroflow xxx.dng` 把 DNG 传给 Gyroflow
- `key code 36` = Enter 键，确认"作为图像序列导入"的弹窗

### Step 3：加载配置预设
```bash
open -a Gyroflow "/path/to/预设.gyroflow"
sleep 12
```
**说明：**
- 用系统 `open` 命令打开 `.gyroflow` 预设文件
- 预设文件包含了镜头校准数据、稳定参数、自动同步设置
- 等待 12 秒让预设完全加载并关闭可能的弹窗

### Step 4：加载运动数据（GCSV）
```python
import pyautogui, time

# 点击运动数据下方的「打开文件」按钮
pyautogui.click(290, 688)    # 坐标因屏幕而异，需要截图测量
time.sleep(6)

# Cmd+Shift+G 打开"前往文件夹"
pyautogui.hotkey('command', 'shift', 'g')
time.sleep(4)

# 逐字输入完整的 GCSV 文件路径
pyautogui.typewrite("/path/to/序列名.gcsv", interval=0.1)
time.sleep(4)

# 第一次按 Enter：关闭"前往文件夹"搜索框
pyautogui.press('return')
time.sleep(3)

# 第二次按 Enter：确认选择文件，关闭文件对话框
pyautogui.press('return')
time.sleep(6)
```
**⚠️ 关键点：**
- `typewrite(interval=0.1)` — 逐字输入，不要用粘贴
- **需要按两次 Enter**
- 点击坐标 `(290, 688)` 在不同屏幕需要重新测量

### Step 5：等待自动同步完成
```python
import subprocess, re, time

def get_gyroflow_cpu():
    """监控 Gyroflow 进程的 CPU 使用率"""
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for line in r.stdout.split('\n'):
        if 'Gyroflow' in line and 'grep' not in line and 'python' not in line:
            parts = re.split(r'\s+', line)
            try: return float(parts[2])
            except: pass
    return None

# 循环等待 CPU 从高负载降到空闲
idle_count = 0
while True:
    cpu = get_gyroflow_cpu()
    if cpu is not None and cpu < 3.0:     # CPU < 3% 表示空闲
        idle_count += 1
        if idle_count >= 3:                # 连续 3 次确认
            break
    else:
        idle_count = 0
    time.sleep(2)
```
**说明：**
- 自动同步时 Gyroflow CPU 可达 200-900%
- 同步完成后 CPU 回落到 0-3%
- 连续 3 次检测到低 CPU 才确认完成

### Step 6：保存项目
```python
import pyautogui, time, subprocess

# 激活 Gyroflow 窗口
subprocess.run(['osascript', '-e', 'tell application "gyroflow" to activate'])
time.sleep(2)

# Cmd+S
pyautogui.hotkey('command', 's')
time.sleep(4)

# 按 Enter 确认保存
pyautogui.press('return')
time.sleep(4)
```
**说明：**
- 文件会自动保存在 DNG 序列同目录下
- 文件名格式：`序列名-%06d.gyroflow`

---

## 三、批量处理脚本

以下是可以直接运行的完整批量脚本：

```python
#!/usr/bin/env python3
"""
Gyroflow 批量处理 DNG 序列帧
"""
import pyautogui, subprocess, time, os, re

pyautogui.FAILSAFE = False

# ===== 配置 =====
PRESET = "/Volumes/mac非遗项目备份/lin/grrofolw-json/24mm.gyroflow"
GCSV_DIR = "/Volumes/xiaomi13/ceshigy"            # GCSV 存放目录
BASE_DIR = "/Volumes/xiaomi13/ceshigy"            # DNG 文件夹所在目录
BTN_COORDS = (290, 688)                           # 运动数据「打开文件」按钮坐标

# 素材列表（按命名规则自动匹配 GCSV）
SEQUENCES = [
    "260516_134158_xiaomi13_24mm",
    "260516_134208_xiaomi13_24mm",
    "260516_134220_xiaomi13_24mm",
]
# ===== 配置结束 =====

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def get_gyroflow_cpu():
    r = subprocess.run(['ps','aux'], capture_output=True, text=True)
    for l in r.stdout.split('\n'):
        if 'Gyroflow' in l and 'grep' not in l and 'python' not in l:
            p = re.split(r'\s+', l)
            try: return float(p[2])
            except: pass
    return None

def wait_for_sync():
    """等待 Gyroflow 自动同步完成"""
    idle = 0
    for _ in range(30):
        cpu = get_gyroflow_cpu()
        if cpu is not None and cpu < 3:
            idle += 1
            if idle >= 3:
                log(f"同步完成 (CPU={cpu}%)")
                return
        else:
            idle = 0
        time.sleep(2)

def process_one(seq_name):
    """处理单个素材"""
    dng = f"{BASE_DIR}/{seq_name}/{seq_name}-000010.dng"
    gcsv = f"{BASE_DIR}/{seq_name}.gcsv"
    
    log(f"=== 处理: {seq_name} ===")
    
    # 关闭已有 Gyroflow
    subprocess.run(['pkill','-9','-f','Gyroflow'], capture_output=True)
    time.sleep(3)
    
    # Step 1-2: DNG
    subprocess.run(['open','-a','Gyroflow']); time.sleep(8)
    subprocess.run(['open','-a','Gyroflow', dng]); time.sleep(8)
    subprocess.run(['osascript','-e','tell application "gyroflow" to activate']); time.sleep(1)
    subprocess.run(['osascript','-e','tell application "System Events" to key code 36']); time.sleep(7)
    log("✅ DNG")
    
    # Step 3: 预设
    subprocess.run(['open','-a','Gyroflow', PRESET]); time.sleep(12)
    log("✅ 预设")
    
    # Step 4: GCSV
    pyautogui.click(BTN_COORDS[0], BTN_COORDS[1]); time.sleep(6)
    pyautogui.hotkey('command','shift','g'); time.sleep(4)
    pyautogui.typewrite(gcsv, interval=0.1); time.sleep(4)
    pyautogui.press('return'); time.sleep(3)   # 关闭前往文件夹
    pyautogui.press('return'); time.sleep(6)   # 确认选择文件
    log("✅ GCSV")
    
    # Step 5: 等同步
    wait_for_sync()
    
    # Step 6: 保存
    subprocess.run(['osascript','-e','tell application "gyroflow" to activate'])
    time.sleep(2)
    pyautogui.hotkey('command','s'); time.sleep(4)
    pyautogui.press('return'); time.sleep(4)
    log("✅ 已保存")
    
    # 验证
    dng_dir = os.path.dirname(dng)
    for f in os.listdir(dng_dir):
        if f.endswith('.gyroflow') and not f.startswith('._'):
            log(f"✅ 文件: {f}")
            break

# 批量处理
for seq in SEQUENCES:
    process_one(seq)
    time.sleep(5)

log("全部处理完成！")
```

---

## 四、在不同电脑上使用

### 需要修改的参数

| 参数 | 说明 |
|------|------|
| `PRESET` | 预设 `.gyroflow` 文件路径 |
| `GCSV_DIR` | GCSV 运动数据存放目录 |
| `BASE_DIR` | DNG 序列帧文件夹所在目录 |
| `BTN_COORDS` | 运动数据「打开文件」按钮的屏幕坐标 |
| `SEQUENCES` | 要处理的素材名称列表 |

### 获取按钮坐标的方法

1. 运行到 **Step 3 完成**（预设加载完毕）
2. 用微信截图（Cmd+Shift+A）框选「运动数据」下方的「打开文件」按钮
3. 截图上会显示选中区域的尺寸和坐标，取中心点坐标

### 文件组织示例

```
GCSV 目录/
├── 序列名1.gcsv          ← GCSV 文件
├── 序列名2.gcsv
└── ...

DNG 目录/
├── 序列名1/              ← DNG 序列帧文件夹
│   ├── 序列名1-000010.dng
│   ├── 序列名1-000011.dng
│   └── ...
├── 序列名2/
│   ├── 序列名2-000010.dng
│   └── ...
└── ...

提示：GCSV 目录和 DNG 目录可以是同一个文件夹，也可以分开。
```

---

## 五、注意事项

1. **输入法**：全程英文输入法（Caps Lock 切换）
2. **间隔时间**：每个操作之间要有足够的等待时间（脚本中已设置）
3. **两次 Enter**：GCSV 文件选择后需要按**两次** Enter
4. **CPU 监控**：自动同步完成通过 CPU 使用率判断，无需固定超时
5. **文件保存**：Cmd+S 后会有 4 秒等待，再按 Enter 确认
6. **按钮坐标**：不同屏幕分辨率需要重新测量 `BTN_COORDS`

---

## 六、工具说明

| 工具 | 用途 |
|------|------|
| `osascript` (AppleScript) | 系统操作（激活窗口、按 Enter、Cmd+S） |
| `open -a` | 打开应用和文件 |
| `pyautogui` | 截图、点击、键盘输入 |
| `pyperclip` | 剪贴板操作（备用输入方式） |
| `ps aux` | 监控 Gyroflow CPU 使用率 |
