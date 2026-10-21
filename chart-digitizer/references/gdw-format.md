# `.gdw` 工作区格式规范（GetData Graph Digitizer）

本文档记录对 **GetData Graph Digitizer 2.24** 的 `.gdw` 文件格式的逆向结论，全部来自本机实测，非猜测。凡标注"实测"的条目均有对应的最小验证脚本支撑。

---

## 1. 总体结构

`.gdw` 是 **UTF-8 编码的 XML**（非二进制、非专有格式），可直接文本编辑与程序生成。

顶层结构：

```xml
<?xml version='1.0' encoding='utf-8'?>
<Workspace>
  <File>
    <Name>C:\path\to\image.bmp</Name>
    <Width>600</Width>
    <Height>450</Height>
  </File>
  <Axes>
    <Xmin>78,392,0</Xmin>
    <Xmax>575,392,10</Xmax>
    <Ymin>78,392,-1.2</Ymin>
    <Ymax>78,15,1.2</Ymax>
    <Xlog>0</Xlog>
    <Ylog>0</Ylog>
  </Axes>
  <Curve>
    <Name>curve1</Name>
    <Color>214,39,40</Color>
    <Points>...</Points>
  </Curve>
  ...
</Workspace>
```

---

## 2. 坐标系统（实测，关键）

### 2.1 `<Points>` 使用原始图像像素坐标

**实测结论：`<Points>` 中记录的点是原始图像的像素坐标，1:1，无缩放、无偏移。**

验证方式：构造已知正弦曲线图，程序生成 `.gdw`，再用 GetData 打开并截图，红色标记点精确落在曲线上；且手工按标定公式换算 `X=0.1 → Y=0.048`，与工作区记录一致。

因此**不需要**考虑 GetData 内部的显示缩放或 DPI 换算。这是与 GetData 对接时最容易误判的一点。

### 2.2 像素 → 数值的映射

四个锚点 `<Xmin>/<Xmax>/<Ymin>/<Ymax>` 各为三元组 `px,py,value`，构成**仿射变换**（支持倾斜轴）：

```
[px]   [x1 y1 1] [a]      value_x 锚点1 = ax*px1 + bx*py1 + cx
[py] = ...       [b]      ...
```

实际采用 4 点求解 6 个仿射参数（每个轴 2 点即可，用 4 点做最小二乘并自校验）。

`chartdigit.calibration.Calibration.from_box()` 是这一映射的实现；`from_axes_pair()` 支持直接给两个锚点。

---

## 3. GetData 的实测怪癖（重要）

### 3.1 `<Points>` 支持小数，但锚点被截断为整数

**实测**：GetData 解析 `<Points>` 的十进制坐标**完全正常**（最小验证：单点 `py=201.50` → 回读 `Y=0.00954907`，与手算精确一致）。

**但**：`<Xmin>/<Xmax>/<Ymin>/<Ymax>` 的像素坐标会被 GetData **截断为整数**，小数部分丢失。

后果：若直接写入浮点锚点，GetData 内部用整数锚点重新投影 `<Points>`，会造成约 **0.003 数据单位**的往返偏差。

**处置**（`getdata.py::_rounded_calibration()`）：生成工作区前，先把锚点像素坐标**自行取整**，再用取整后的锚点把每个点从"原生像素"重投影为"新像素"。这样 GetData 读到的锚点与点集**自洽**，往返偏差降至 **3.2e-05 数据单位**（实测，较处理前改善约 100 倍）。

```
原生点 → (标定) → 数据值 → (取整后的标定反变换) → 新像素 → 写入 <Points>
```

### 3.2 `<File><Name>` 相对于**进程工作目录**解析

**实测**：GetData 打开 `.gdw` 时，`<File><Name>` 若为相对路径，是相对于**GetData 进程的当前工作目录**，而**不是** `.gdw` 文件所在目录。

后果：相对路径几乎必然报 "Unable to open file" 弹窗。

**处置**：`build_workspace()` 一律写入**绝对路径**。

### 3.3 原生格式不含 PNG

GetData 2.24 支持的图像格式：**JPG / JPEG / TIF / TIFF / BMP / PCX**。

**不支持 PNG、GIF、WEBP**。

**处置**：`getdata.py::_safe_image_for_getdata()` 在生成工作区前自动把非原生格式转为 BMP 放到工作目录（`prefer_native=True`），并在 `.gdw` 中指向转换后的文件。native 引擎不受此限制（Pillow 可读任意格式）。

### 3.4 `<Color>` 格式

`R,G,B` 逗号分隔十进制。缺失时 GetData 用默认色。`_parse_color()` 容错处理空值与非法格式。

### 3.5 `<Points>` 分段

`POINTS_PER_SEGMENT = 10`：点集按每 10 点一段、段间以分隔符断开，用于表示曲线的不连续区间（避免把断点连成直线）。

---

## 4. 最小可用 `.gdw` 模板

```xml
<?xml version='1.0' encoding='utf-8'?>
<Workspace>
  <File>
    <Name>C:\abs\path\to\image.bmp</Name>
    <Width>600</Width>
    <Height>450</Height>
  </File>
  <Axes>
    <Xmin>78,392,0</Xmin>
    <Xmax>575,392,10</Xmax>
    <Ymin>78,392,-1.2</Ymin>
    <Ymax>78,15,1.2</Ymax>
    <Xlog>0</Xlog>
    <Ylog>0</Ylog>
  </Axes>
  <Curve>
    <Name>c1</Name>
    <Color>31,119,180</Color>
    <Points>100 300
101 299.5
102 299
...
</Points>
  </Curve>
</Workspace>
```

注意：`<Name>` 用绝对路径；锚点像素坐标为整数；`<Points>` 每行 `px py`，允许小数。

---

## 5. 程序对接入口

```python
from chartdigit import build_workspace, launch, read_workspace

# 生成（自动处理格式转换与锚点取整）
build_workspace("figure.png", series, calib, "out.gdw")

# 打开 GUI 复核
launch("out.gdw", wait=False, maximize=True)

# 复核后回读
digitized = read_workspace("out.gdw", sort_by_x=True)
```

`read_workspace()` 内部按当前工作区的锚点重新标定，因此人工在 GUI 中的任何修正都会被正确反映。
