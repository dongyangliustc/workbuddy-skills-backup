Attribute VB_Name = "PESTuner"
Option Explicit

' ============================================================
' PES Tuner v2 — 零外部引用的后期绑定版
'
' ⚠️ 状态: 开发中 (In Development)
' ⚠️ 已知问题: Office 2016 Build 14334 对 CommandBars API 兼容性存在限制，
'    部分环境加载时可能触发 "隐藏的模块中的编译错误"。
' ⚠️ 隔离说明: 此文件暂不与 pes_sketch.py 核心生成流程联动，
'    请勿将其作为公开发布功能的一部分。
' ⚠️ 如需在当前环境使用，建议:
'    1. 打开 PPT → 开发工具 → Visual Basic → 工具 → 引用
'    2. 检查是否有标记为 "MISSING" 的引用
'    3. 取消勾选所有丢失的引用后重新编译
'
' 稳定版本请使用 dist/PES_Tuner.exe (桌面 GUI 工具)
' 或 scripts/pes_resizer.py (CLI 工具)
' ============================================================

' ---- Office 枚举常量（硬编码，避免引用 MSO.DLL）----
Private Const MSO_CONTROL_BUTTON As Long = 1
Private Const MSO_BAR_TOP As Long = 0
Private Const MSO_BUTTON_ICON_AND_CAPTION As Long = 1
Private Const MSO_CONNECTOR_TYPE As Long = 1
Private Const MSO_LINE_SOLID As Long = 1

' ---- 模块级变量 ----
Private gFont As String
Private gSizeStr As String
Private gMWStr As String
Private gDWStr As String

' ============================================================
' 加载 / 卸载
' ============================================================

Public Sub Auto_Open()
    gFont = "Times New Roman"
    gSizeStr = "11"
    gMWStr = "3"
    gDWStr = "3"
    CreateCommandBar
End Sub

Public Sub Auto_Close()
    RemoveCommandBar
End Sub

' ============================================================
' CommandBar 创建/删除（全部后期绑定 Object 类型）
' ============================================================

Private Sub CreateCommandBar()
    On Error Resume Next
    RemoveCommandBar
    On Error GoTo 0

    ' 后期绑定：Application.CommandBars 返回 Object
    Dim cb As Object
    Dim btn As Object

    On Error Resume Next
    Set cb = Application.CommandBars("PES Tuner")
    If cb Is Nothing Then
        Set cb = Application.CommandBars.Add("PES Tuner", MSO_BAR_TOP, False, True)
        cb.Visible = True
    End If
    On Error GoTo 0

    If cb Is Nothing Then Exit Sub

    ' Button 1: Scan
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Scan Slide"
    btn.FaceId = 941
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnQuickScan"
    btn.Tag = "PES_Scan"

    ' Button 2: Font
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Set Font"
    btn.FaceId = 263
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnSetFont"
    btn.Tag = "PES_Font"

    ' Button 3: Size
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Set Size"
    btn.FaceId = 262
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnSetSize"
    btn.Tag = "PES_Size"

    ' Button 4: Marker Width
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Marker Width"
    btn.FaceId = 837
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnSetMarkerWidth"
    btn.Tag = "PES_Marker"

    ' Button 5: Dash Width
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Dash Width"
    btn.FaceId = 838
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnSetDashWidth"
    btn.Tag = "PES_Dash"

    ' Button 6: Apply All
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Apply All"
    btn.FaceId = 281
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnApply"
    btn.Tag = "PES_Apply"

    ' Button 7: Align Ends
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Align Ends"
    btn.FaceId = 707
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnReAlign"
    btn.Tag = "PES_Align"

    ' Button 8: Glue
    Set btn = cb.Controls.Add(MSO_CONTROL_BUTTON)
    btn.Caption = "Glue"
    btn.FaceId = 143
    btn.Style = MSO_BUTTON_ICON_AND_CAPTION
    btn.OnAction = "OnGlue"
    btn.Tag = "PES_Glue"
End Sub

Private Sub RemoveCommandBar()
    On Error Resume Next
    Application.CommandBars("PES Tuner").Delete
    On Error GoTo 0
End Sub

' ============================================================
' 回调
' ============================================================

Public Sub OnQuickScan()
    ScanSlide True
End Sub

Public Sub OnSetFont()
    Dim ans As String
    ans = InputBox("Enter font name:", "PES Tuner", gFont)
    If ans <> "" Then gFont = ans
End Sub

Public Sub OnSetSize()
    Dim ans As String
    ans = InputBox("Enter font size (pt):", "PES Tuner", gSizeStr)
    If IsNumeric(ans) And ans <> "" Then gSizeStr = ans
End Sub

Public Sub OnSetMarkerWidth()
    Dim ans As String
    ans = InputBox("Enter marker line width (pt):", "PES Tuner", gMWStr)
    If IsNumeric(ans) And ans <> "" Then gMWStr = ans
End Sub

Public Sub OnSetDashWidth()
    Dim ans As String
    ans = InputBox("Enter dashed line width (pt):", "PES Tuner", gDWStr)
    If IsNumeric(ans) And ans <> "" Then gDWStr = ans
End Sub

' ============================================================
' 扫描
' ============================================================

Public Sub ScanSlide(Optional showMsg As Boolean = False)
    Dim sld As Object  ' Slide
    Dim s As Object    ' Shape
    Dim markers As Long, dashed As Long, texts As Long

    On Error GoTo ErrHandler
    Set sld = ActiveWindow.View.Slide
    For Each s In sld.Shapes
        If s.Type = MSO_CONNECTOR_TYPE Then
            If s.Height < 6 And s.Width > 30 And s.Width < 250 Then
                markers = markers + 1
            End If
            If s.Line.DashStyle <> MSO_LINE_SOLID Then
                dashed = dashed + 1
            End If
        End If
        If s.HasTextFrame Then
            If s.TextFrame.HasText Then
                texts = texts + 1
            End If
        End If
    Next
    If showMsg Then
        MsgBox "Markers:" & markers & "  Dashed:" & dashed & "  Texts:" & texts, _
               vbInformation, "PES Tuner"
    End If
    Exit Sub
ErrHandler:
    MsgBox "Select a slide first", vbExclamation, "PES Tuner"
End Sub

' ============================================================
' 获取幻灯片
' ============================================================

' 修复：改回 Slide 类型。PPT 类型库始终可用，Object 导致 ConnectorFormat.BeginConnect 参数编组失败
Private Function GetSlide() As Slide
    On Error Resume Next
    Set GetSlide = ActiveWindow.View.Slide
End Function

' ============================================================
' 一键应用
' ============================================================

' 修复：改回 Slide/Shape 类型，避免 TextFrame.Font 链式后期绑定失败
Public Sub OnApply()
    Dim sld As Slide
    Set sld = GetSlide()
    If sld Is Nothing Then Exit Sub

    Application.ScreenUpdating = False
    Dim s As Shape, fs As Single, mw As Single, dw As Single
    If IsNumeric(gSizeStr) Then fs = CSng(gSizeStr)
    If IsNumeric(gMWStr) Then mw = CSng(gMWStr)
    If IsNumeric(gDWStr) Then dw = CSng(gDWStr)

    Dim mt As Long, md As Long, mx As Long
    For Each s In sld.Shapes
        If s.HasTextFrame And s.TextFrame.HasText Then
            With s.TextFrame.TextRange.Font
                .Name = gFont
                If fs > 0 Then .Size = fs
            End With
            mx = mx + 1
        End If
        If s.Type = MSO_CONNECTOR_TYPE Then
            If s.Height < 6 And s.Width > 30 And s.Width < 250 Then
                If mw > 0 Then s.Line.Weight = mw
                mt = mt + 1
            End If
            If s.Line.DashStyle <> MSO_LINE_SOLID Then
                If dw > 0 Then s.Line.Weight = dw
                md = md + 1
            End If
        End If
    Next
    Application.ScreenUpdating = True
    MsgBox "Done! Fonts:" & mx & "  Markers:" & mt & "  Dashed:" & md, _
           vbInformation, "PES Tuner"
End Sub

' ============================================================
' 对齐 / 粘合
' ============================================================

' 修复：改回 Slide/Shape 类型。ReDim Preserve 在 Object 上下文中可能导致内部类型信息丢失
Public Sub OnReAlign()
    Dim sld As Slide
    Set sld = GetSlide()
    If sld Is Nothing Then Exit Sub

    Application.ScreenUpdating = False
    Dim markers() As Single, mCount As Long: mCount = 0
    ReDim markers(1, 0)

    Dim s As Shape
    For Each s In sld.Shapes
        If s.Type = MSO_CONNECTOR_TYPE Then
            If s.Height < 6 And s.Width > 30 And s.Width < 250 Then
                If mCount = 0 Then
                    ReDim markers(1 To 3, 0 To 0)
                Else
                    ReDim Preserve markers(1 To 3, 0 To mCount)
                End If
                markers(1, mCount) = s.Left
                markers(2, mCount) = s.Left + s.Width
                markers(3, mCount) = s.Top
                mCount = mCount + 1
            End If
        End If
    Next

    If mCount < 2 Then
        Application.ScreenUpdating = True
        MsgBox "Need at least 2 markers", vbExclamation, "PES Tuner"
        Exit Sub
    End If

    Dim aligned As Long
    For Each s In sld.Shapes
        If s.Type = MSO_CONNECTOR_TYPE And s.Line.DashStyle <> MSO_LINE_SOLID Then
            Dim nx1 As Single, ny1 As Single, nx2 As Single, ny2 As Single
            Dim d1 As Single, d2 As Single
            FindNearestMarker s.Left, s.Top, markers, mCount, nx1, ny1, d1
            FindNearestMarker s.Left + s.Width, s.Top + s.Height, markers, mCount, nx2, ny2, d2
            If d1 < 200 And d2 < 200 Then
                s.Left = Min(nx1, nx2): s.Top = Min(ny1, ny2)
                s.Width = Abs(nx2 - nx1): s.Height = Abs(ny2 - ny1)
                aligned = aligned + 1
            End If
        End If
    Next
    Application.ScreenUpdating = True
    MsgBox "Aligned " & aligned & " connectors", vbInformation, "PES Tuner"
End Sub

' 修复：改回 Slide/Shape 类型。BeginConnect/EndConnect 要求 Shape 类型参数，Object 导致 COM 编组失败
Public Sub OnGlue()
    Dim sld As Slide
    Set sld = GetSlide()
    If sld Is Nothing Then Exit Sub

    Application.ScreenUpdating = False
    Dim markers() As Single, mCount As Long: mCount = 0
    ReDim markers(1, 0)
    Dim s As Shape
    For Each s In sld.Shapes
        If s.Type = MSO_CONNECTOR_TYPE And s.Height < 6 And s.Width > 30 And s.Width < 250 Then
            If mCount = 0 Then
                ReDim markers(1 To 3, 0 To 0)
            Else
                ReDim Preserve markers(1 To 3, 0 To mCount)
            End If
            markers(1, mCount) = s.Left
            markers(2, mCount) = s.Left + s.Width
            markers(3, mCount) = s.Top
            mCount = mCount + 1
        End If
    Next
    If mCount < 2 Then
        Application.ScreenUpdating = True
        MsgBox "Need at least 2 markers", vbExclamation, "PES Tuner"
        Exit Sub
    End If

    ' 修复：sM/eM 改回 Shape 类型，确保 BeginConnect 参数类型正确
    Dim glued As Long, sM As Shape, eM As Shape
    For Each s In sld.Shapes
        If s.Type = MSO_CONNECTOR_TYPE And s.Line.DashStyle <> MSO_LINE_SOLID Then
            Set sM = NearestShape(sld, s.Left, s.Top, markers, mCount)
            Set eM = NearestShape(sld, s.Left + s.Width, s.Top + s.Height, markers, mCount)
            If Not sM Is Nothing And Not eM Is Nothing Then
                On Error Resume Next
                If s.ConnectorFormat.BeginConnected Then s.ConnectorFormat.BeginDisconnect
                If s.ConnectorFormat.EndConnected Then s.ConnectorFormat.EndDisconnect
                s.ConnectorFormat.BeginConnect sM, 1
                s.ConnectorFormat.EndConnect eM, 1
                If Err.Number = 0 Then glued = glued + 1 Else Err.Clear
                On Error GoTo 0
            End If
        End If
    Next
    Application.ScreenUpdating = True
    MsgBox "Glued " & glued & " connectors", vbInformation, "PES Tuner"
End Sub

' ============================================================
' 辅助
' ============================================================

Private Function Min(a As Single, b As Single) As Single
    If a < b Then Min = a Else Min = b
End Function

Private Sub FindNearestMarker(tx As Single, ty As Single, _
    markers() As Single, mCount As Long, _
    ByRef ox As Single, ByRef oy As Single, ByRef dist As Single)

    Dim i As Long, d As Single
    dist = 999999
    For i = 0 To mCount - 1
        d = Sqr((markers(1, i) - tx) ^ 2 + (markers(3, i) - ty) ^ 2)
        If d < dist Then
            dist = d: ox = markers(1, i): oy = markers(3, i)
        End If
        d = Sqr((markers(2, i) - tx) ^ 2 + (markers(3, i) - ty) ^ 2)
        If d < dist Then
            dist = d: ox = markers(2, i): oy = markers(3, i)
        End If
    Next
End Sub

' 修复：参数和返回值改回 Slide/Shape 类型
Private Function NearestShape(sld As Slide, tx As Single, ty As Single, _
    markers() As Single, mCount As Long) As Shape

    Dim i As Long, d As Single, bestD As Single: bestD = 999999
    Dim bestI As Long: bestI = -1
    For i = 0 To mCount - 1
        d = Sqr((markers(1, i) - tx) ^ 2 + (markers(3, i) - ty) ^ 2)
        If d < bestD Then bestD = d: bestI = i
        d = Sqr((markers(2, i) - tx) ^ 2 + (markers(3, i) - ty) ^ 2)
        If d < bestD Then bestD = d: bestI = i
    Next
    If bestI < 0 Or bestD > 200 Then
        Set NearestShape = Nothing
        Exit Function
    End If

    ' 修复：循环变量改回 Shape 类型
    Dim s As Shape, fIdx As Long: fIdx = 0
    For Each s In sld.Shapes
        If s.Type = MSO_CONNECTOR_TYPE And s.Height < 6 And s.Width > 30 And s.Width < 250 Then
            If fIdx = bestI Then
                Set NearestShape = s
                Exit Function
            End If
            fIdx = fIdx + 1
        End If
    Next
End Function
