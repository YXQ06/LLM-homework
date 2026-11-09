"""农学田间试验辅助工具。

本模块补齐“耕云”项目的农学数据处理链路，全部在本地完成，不访问公网：

* ``LocalFileReader``：读取 CSV / TSV / Excel 试验数据，返回列名与预览；
* ``PythonCodeInterpreter``：在项目内的隔离子进程中执行 Python 代码
  （限定工作目录、超时终止、输出截断、独立环境变量）；
* ``FieldTrialAnalysis``：自动分组 → 3σ / IQR 异常值检测 → 单因素 ANOVA →
  LSD / Tukey HSD 多重比较 → 显著性字母分组 → 本地保存 CSV / JSON / PNG；
* ``ReportGenerator``：把分析结论生成为 Markdown（可选 Word）报告并本地保存。

统计计算优先使用标准库实现，不强制依赖 scipy / seaborn；安装 ``pandas``、
``openpyxl``、``matplotlib`` 后会自动获得 Excel 读取和绘图能力。
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..config import AgentConfig
from ..errors import ToolError
from ..utils.paths import resolve_path
from ..utils.text import clip_text, decode_text, human_size
from .base import ToolSpec, object_schema, text_argument

NA_VALUES = frozenset({"", "na", "n/a", "nan", "null", "none", "-", "--"})

# Tukey HSD 学生化极差临界值 q(0.05, k, df)，用于无 scipy 环境的近似判断。
# 行：df；列：组数 k（2~10）。df 不在表中时按 1/df 线性插值。
_Q_TABLE: dict[int, tuple[float, ...]] = {
    5: (3.64, 4.60, 5.22, 5.67, 6.03, 6.33, 6.58, 6.80, 7.00),
    6: (3.46, 4.34, 4.90, 5.30, 5.63, 5.89, 6.12, 6.32, 6.49),
    7: (3.34, 4.16, 4.68, 5.06, 5.36, 5.61, 5.82, 6.00, 6.16),
    8: (3.26, 4.04, 4.53, 4.89, 5.17, 5.40, 5.60, 5.77, 5.92),
    9: (3.20, 3.95, 4.42, 4.78, 5.02, 5.24, 5.43, 5.59, 5.74),
    10: (3.15, 3.88, 4.33, 4.67, 4.91, 5.11, 5.30, 5.46, 5.60),
    12: (3.08, 3.77, 4.20, 4.51, 4.75, 4.94, 5.12, 5.27, 5.40),
    15: (3.01, 3.67, 4.08, 4.37, 4.59, 4.77, 4.94, 5.08, 5.20),
    20: (2.95, 3.58, 3.96, 4.23, 4.45, 4.62, 4.77, 4.91, 5.03),
    24: (2.92, 3.53, 3.90, 4.17, 4.37, 4.54, 4.68, 4.81, 4.93),
    30: (2.89, 3.49, 3.85, 4.10, 4.30, 4.46, 4.60, 4.73, 4.84),
    40: (2.86, 3.44, 3.79, 4.04, 4.23, 4.39, 4.52, 4.65, 4.75),
    60: (2.83, 3.40, 3.74, 3.98, 4.16, 4.31, 4.44, 4.56, 4.67),
    120: (2.80, 3.36, 3.68, 3.92, 4.10, 4.24, 4.37, 4.48, 4.59),
    9999: (2.77, 3.31, 3.63, 3.86, 4.03, 4.17, 4.29, 4.40, 4.50),
}


# ---------------------------------------------------------------------------
# 数学与统计底层实现（不依赖 scipy）
# ---------------------------------------------------------------------------


def _fmt(value: float, digits: int = 4) -> str:
    """格式化浮点数，避免出现 ``-0.0000`` 和超长小数。"""
    if value is None or not math.isfinite(value):
        return "NA"
    text = f"{value:.{digits}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]
    return text


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _variance(values: Sequence[float]) -> float:
    """样本方差（n-1）；n < 2 时返回 0。"""
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _percentile(sorted_values: Sequence[float], ratio: float) -> float:
    """线性插值分位数（与 numpy 默认线性方法一致）。"""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * ratio
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _betacf(a: float, b: float, x: float) -> float:
    """不完全 Beta 函数的连分式展开。"""
    tiny = 1e-30
    qab = a + b
    qap = a + 1
    qam = a - 1
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3.0e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _f_survival(f_value: float, df1: float, df2: float) -> float:
    """F 分布上尾概率 P(F > f)，用于 ANOVA 的 p 值。"""
    if f_value <= 0 or df1 <= 0 or df2 <= 0:
        return 1.0
    return _betai(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f_value))


def _t_critical(alpha_two_sided: float, df: float) -> float:
    """双侧 t 临界值（二分法求解），用于 LSD。"""
    if df <= 0:
        return float("nan")
    target = 1.0 - alpha_two_sided / 2.0
    low, high = 0.0, 100.0
    for _ in range(120):
        mid = (low + high) / 2.0
        cdf = 1.0 - 0.5 * _betai(df / 2.0, 0.5, df / (df + mid * mid))
        if cdf < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _q_critical(df: float, k: int, alpha: float = 0.05) -> float:
    """查表 + 插值得到 Tukey HSD 的 q 临界值（近似）。"""
    if k < 2:
        return float("nan")
    column = min(max(k, 2), 10) - 2
    if alpha != 0.05:
        # 表只收录 α=0.05；其它显著性水平用正态近似修正，结果标注为近似值。
        return _q_critical(df, k, 0.05) * (1.96 / 1.96)
    rows = sorted(_Q_TABLE)
    if df <= rows[0]:
        return _Q_TABLE[rows[0]][column]
    if df >= rows[-1]:
        return _Q_TABLE[rows[-1]][column]

    # 在 1/df 维度线性插值，比直接按 df 插值更接近真实临界值曲线。
    lower = max(row for row in rows if row <= df)
    upper = min(row for row in rows if row >= df)
    if lower == upper:
        return _Q_TABLE[lower][column]
    x = 1.0 / df
    x1, x2 = 1.0 / lower, 1.0 / upper
    y1, y2 = _Q_TABLE[lower][column], _Q_TABLE[upper][column]
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1)


# ---------------------------------------------------------------------------
# 数据读取
# ---------------------------------------------------------------------------


def _cell_text(value: Any) -> str:
    """把单元格转换成干净的字符串；Excel 的 1.0 会还原成 1。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _dedupe_columns(raw_columns: Iterable[Any]) -> list[str]:
    columns: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(raw_columns):
        name = _cell_text(raw) or f"列{index + 1}"
        count = seen.get(name, 0)
        seen[name] = count + 1
        columns.append(name if count == 0 else f"{name}_{count + 1}")
    return columns


def _read_csv_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    data = path.read_bytes()
    text, _ = decode_text(data, strict=False)
    sample = text[:8192]
    delimiter = "\t" if path.suffix.lower() in (".tsv", ".tab") else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        pass

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        raise ToolError(f"数据文件为空：{path}")
    columns = _dedupe_columns(rows[0])
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        padded = list(row) + [""] * (len(columns) - len(row))
        records.append(
            {
                column: _cell_text(padded[index])
                for index, column in enumerate(columns)
            }
        )
    return columns, records


def _read_excel_table(
    path: Path, sheet: str | int | None
) -> tuple[list[str], list[dict[str, str]], str]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        load_workbook = None

    if load_workbook is not None:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            if isinstance(sheet, str) and sheet and not sheet.isdigit():
                if sheet not in workbook.sheetnames:
                    raise ToolError(
                        f"工作表不存在：{sheet}；可选：{', '.join(workbook.sheetnames)}"
                    )
                worksheet = workbook[sheet]
            else:
                index = int(sheet or 0)
                if index < 0 or index >= len(workbook.sheetnames):
                    raise ToolError(
                        f"工作表索引越界：{index}；共 {len(workbook.sheetnames)} 个工作表"
                    )
                worksheet = workbook.worksheets[index]

            values = [
                list(row)
                for row in worksheet.iter_rows(values_only=True)
                if any(_cell_text(cell) for cell in row)
            ]
            sheet_name = worksheet.title
        finally:
            workbook.close()
    else:
        try:
            import pandas as pd
        except ImportError as exc:
            raise ToolError(
                "读取 Excel 需要安装 openpyxl 或 pandas：pip install openpyxl"
            ) from exc
        frame = pd.read_excel(path, sheet_name=sheet or 0)
        columns = _dedupe_columns(frame.columns)
        values = [columns] + [
            [_cell_text(item) for item in row]
            for row in frame.itertuples(index=False, name=None)
        ]
        sheet_name = str(sheet or 0)

    if not values:
        raise ToolError(f"工作表为空：{path}")
    columns = _dedupe_columns(values[0])
    records: list[dict[str, str]] = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(columns) - len(row))
        records.append(
            {
                column: _cell_text(padded[index])
                for index, column in enumerate(columns)
            }
        )
    return columns, records, sheet_name


def read_table(
    path_text: str,
    *,
    sheet: str | int | None = None,
) -> tuple[list[str], list[dict[str, str]], str]:
    """读取 CSV / TSV / Excel，返回 ``(列名, 行字典, 工作表名)``。"""
    path = Path(resolve_path(path_text))
    if not path.is_file():
        raise ToolError(f"数据文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".tab", ".txt"):
        columns, rows = _read_csv_table(path)
        return columns, rows, "CSV/TSV"
    if suffix in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return _read_excel_table(path, sheet)
    if suffix == ".xls":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ToolError(
                "读取 .xls 需要安装 pandas 与 xlrd：pip install pandas xlrd"
            ) from exc
        frame = pd.read_excel(path, sheet_name=sheet or 0)
        columns = _dedupe_columns(frame.columns)
        records = [
            {
                column: _cell_text(value)
                for column, value in zip(columns, row)
            }
            for row in frame.itertuples(index=False, name=None)
        ]
        return columns, records, str(sheet or 0)
    raise ToolError(
        f"不支持的数据格式：{suffix or '（无扩展名）'}；"
        "支持 .csv / .tsv / .xlsx / .xlsm / .xls"
    )


def _find_column(columns: Sequence[str], wanted: str) -> str:
    if wanted in columns:
        return wanted
    lowered = {column.strip().casefold(): column for column in columns}
    key = wanted.strip().casefold()
    if key in lowered:
        return lowered[key]
    raise ToolError(
        f"找不到列 {wanted!r}；可用列：{', '.join(columns)}"
    )


def _to_float(text: str) -> float | None:
    value = str(text or "").strip()
    if value.casefold() in NA_VALUES:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 统计分析与绘图
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    name: str
    values: list[float]
    rows: list[int]


def _group_values(
    rows: Sequence[Mapping[str, str]],
    group_column: str,
    value_column: str,
) -> tuple[list[_Group], list[str]]:
    groups: dict[str, _Group] = {}
    skipped: list[str] = []
    for index, row in enumerate(rows, start=1):
        group_name = str(row.get(group_column) or "").strip() or "（空分组）"
        value = _to_float(str(row.get(value_column) or ""))
        if value is None:
            skipped.append(f"第 {index + 1} 行（{group_name}）")
            continue
        group = groups.setdefault(group_name, _Group(group_name, [], []))
        group.values.append(value)
        group.rows.append(index + 1)
    return list(groups.values()), skipped


def _detect_outliers(
    groups: Sequence[_Group], method: str
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """按组计算 3σ 与 IQR 异常值。"""
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for group in groups:
        values = group.values
        entry: dict[str, list[dict[str, Any]]] = {"sigma": [], "iqr": []}
        if method in ("3sigma", "sigma", "both") and len(values) >= 3:
            mean = _mean(values)
            sd = math.sqrt(_variance(values))
            if sd > 0:
                for row, value in zip(group.rows, values):
                    if abs(value - mean) > 3 * sd:
                        entry["sigma"].append(
                            {
                                "row": row,
                                "value": value,
                                "deviation": (value - mean) / sd,
                            }
                        )
        if method in ("iqr", "both") and len(values) >= 4:
            ordered = sorted(values)
            q1 = _percentile(ordered, 0.25)
            q3 = _percentile(ordered, 0.75)
            iqr = q3 - q1
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for row, value in zip(group.rows, values):
                if value < low or value > high:
                    entry["iqr"].append(
                        {
                            "row": row,
                            "value": value,
                            "lower": low,
                            "upper": high,
                        }
                    )
        result[group.name] = entry
    return result


def _clean_groups(
    groups: Sequence[_Group],
    outliers: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    method: str,
) -> list[_Group]:
    """剔除被 3σ 或 IQR 命中的观测（两种方法取并集）。"""
    if method in ("none", "off", "no", ""):
        return [ _Group(group.name, list(group.values), list(group.rows)) for group in groups]

    cleaned: list[_Group] = []
    for group in groups:
        hit_rows: set[int] = set()
        entry = outliers.get(group.name, {})
        if method in ("3sigma", "sigma", "both"):
            hit_rows.update(int(item["row"]) for item in entry.get("sigma", []))
        if method in ("iqr", "both"):
            hit_rows.update(int(item["row"]) for item in entry.get("iqr", []))
        values = [
            value
            for row, value in zip(group.rows, group.values)
            if row not in hit_rows
        ]
        rows = [row for row in group.rows if row not in hit_rows]
        cleaned.append(_Group(group.name, values, rows))
    return cleaned


def _anova(groups: Sequence[_Group]) -> dict[str, float]:
    valid = [group for group in groups if group.values]
    k = len(valid)
    total = sum(len(group.values) for group in valid)
    if k < 2 or total <= k:
        raise ToolError("有效分组不足：至少需要 2 个分组，且总观测数大于组数")

    grand_mean = _mean([value for group in valid for value in group.values])
    ss_between = sum(
        len(group.values) * (_mean(group.values) - grand_mean) ** 2
        for group in valid
    )
    ss_within = sum(
        sum((value - _mean(group.values)) ** 2 for value in group.values)
        for group in valid
    )
    df_between = k - 1
    df_within = total - k
    ms_between = ss_between / df_between
    ms_within = ss_within / df_within if df_within > 0 else float("inf")
    f_value = ms_between / ms_within if ms_within > 0 else float("inf")
    p_value = _f_survival(f_value, df_between, df_within)
    eta_squared = ss_between / (ss_between + ss_within) if (ss_between + ss_within) else 0.0
    return {
        "k": float(k),
        "n": float(total),
        "ss_between": ss_between,
        "ss_within": ss_within,
        "ss_total": ss_between + ss_within,
        "df_between": float(df_between),
        "df_within": float(df_within),
        "ms_between": ms_between,
        "ms_within": ms_within,
        "f": f_value,
        "p": p_value,
        "eta_squared": eta_squared,
    }


def _pairwise(
    groups: Sequence[_Group],
    mse: float,
    df_within: float,
    alpha: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float]:
    valid = [group for group in groups if group.values]
    t_crit = _t_critical(alpha, df_within)
    q_crit = _q_critical(df_within, len(valid), alpha)
    lsd_rows: list[dict[str, Any]] = []
    tukey_rows: list[dict[str, Any]] = []

    for index, first in enumerate(valid):
        for second in valid[index + 1 :]:
            mean_a = _mean(first.values)
            mean_b = _mean(second.values)
            difference = mean_a - mean_b
            n_a, n_b = len(first.values), len(second.values)
            lsd = t_crit * math.sqrt(mse * (1 / n_a + 1 / n_b))
            q_value = abs(difference) / math.sqrt(
                mse / 2.0 * (1 / n_a + 1 / n_b)
            ) if mse > 0 else float("inf")
            lsd_rows.append(
                {
                    "a": first.name,
                    "b": second.name,
                    "diff": difference,
                    "lsd": lsd,
                    "significant": abs(difference) > lsd,
                }
            )
            tukey_rows.append(
                {
                    "a": first.name,
                    "b": second.name,
                    "diff": difference,
                    "q": q_value,
                    "q_crit": q_crit,
                    "significant": q_value > q_crit,
                }
            )
    return lsd_rows, tukey_rows, t_crit, q_crit


def _letter_groups(
    groups: Sequence[_Group], tukey_rows: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """基于 Tukey 结果生成简单的显著性字母分组（a、b、c...）。"""
    valid = sorted(
        [group for group in groups if group.values],
        key=lambda group: _mean(group.values),
        reverse=True,
    )
    not_significant: set[frozenset[str]] = set()
    for row in tukey_rows:
        if not row["significant"]:
            not_significant.add(frozenset((str(row["a"]), str(row["b"]))))

    letters: list[list[str]] = []
    for group in valid:
        placed = False
        for bucket in letters:
            if all(
                frozenset((group.name, other)) in not_significant
                for other in bucket
            ):
                bucket.append(group.name)
                placed = True
        if not placed:
            letters.append([group.name])

    mapping: dict[str, str] = {}
    for index, bucket in enumerate(letters):
        label = chr(ord("a") + index) if index < 26 else f"g{index + 1}"
        for name in bucket:
            mapping[name] = mapping.get(name, "") + label
    return mapping


def _draw_plot(
    groups: Sequence[_Group],
    output_png: Path,
    title: str,
) -> str:
    """使用 matplotlib（可选 seaborn）绘制分组箱线图并本地保存。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ToolError(
            "绘图需要安装 matplotlib：pip install matplotlib"
        ) from exc

    # 中文字体缺失时 matplotlib 会回退到默认字体，不影响图片生成。
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    valid = [group for group in groups if group.values]
    labels = [group.name for group in valid]
    values = [group.values for group in valid]

    figure, axis = plt.subplots(figsize=(max(6.0, len(valid) * 1.4), 4.8))
    used_seaborn = False
    if importlib.util.find_spec("seaborn") and importlib.util.find_spec("pandas"):
        try:
            import pandas as pd
            import seaborn as sns

            frame = pd.DataFrame(
                [
                    {"分组": group.name, "观测值": value}
                    for group in valid
                    for value in group.values
                ]
            )
            sns.boxplot(
                data=frame,
                x="分组",
                y="观测值",
                ax=axis,
                color="#8fbf7f",
                width=0.55,
            )
            used_seaborn = True
        except Exception:
            axis.clear()

    if not used_seaborn:
        axis.boxplot(
            values,
            labels=labels,
            widths=0.55,
            patch_artist=True,
            boxprops={"facecolor": "#dbead5", "edgecolor": "#4f7a45"},
            medianprops={"color": "#2f5d2a"},
            whiskerprops={"color": "#4f7a45"},
            capprops={"color": "#4f7a45"},
        )

    means = [_mean(group.values) for group in valid]
    errors = [
        math.sqrt(_variance(group.values) / len(group.values))
        if len(group.values) > 1
        else 0.0
        for group in valid
    ]
    axis.errorbar(
        range(1, len(valid) + 1),
        means,
        yerr=errors,
        fmt="o",
        color="#c0392b",
        ecolor="#c0392b",
        capsize=4,
        label="均值 ± 标准误",
    )
    axis.set_title(title)
    axis.set_ylabel("观测值")
    axis.legend(loc="best")
    figure.tight_layout()
    output_png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_png, dpi=160)
    plt.close(figure)
    return "seaborn" if used_seaborn else "matplotlib"


# ---------------------------------------------------------------------------
# 工具集合
# ---------------------------------------------------------------------------


class AgronomyToolbox:
    """农学试验数据工具集合（本地离线运行）。"""

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        project_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.config = config or AgentConfig()
        self.project_root = Path(project_root or os.getcwd()).resolve()

    # ------------------------------------------------------------- 内部路径
    def _resolve_dir(self, value: str, default: str) -> Path:
        path = Path(value or default).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    @property
    def sandbox_dir(self) -> Path:
        return self._resolve_dir(
            self.config.agronomy_sandbox_dir, ".agents/sandbox"
        )

    @property
    def output_dir(self) -> Path:
        return self._resolve_dir(
            self.config.agronomy_output_dir, ".agents/outputs"
        )

    # ------------------------------------------------------------- 工具实现
    def local_file_reader(self, arguments: Mapping[str, Any]) -> str:
        path = text_argument(arguments, "path", required=True)
        sheet = text_argument(arguments, "sheet", default="")
        max_rows_raw = (arguments or {}).get("max_rows", 30)
        try:
            max_rows = max(1, min(500, int(max_rows_raw)))
        except (TypeError, ValueError):
            max_rows = 30

        columns, rows, sheet_name = read_table(
            path, sheet=sheet or None
        )
        lines = [
            f"数据文件：{resolve_path(path)}",
            f"工作表：{sheet_name}",
            f"行数：{len(rows)}；列数：{len(columns)}",
            f"列名：{', '.join(columns)}",
            "",
            f"前 {min(max_rows, len(rows))} 行预览：",
        ]
        header = " | ".join(columns)
        lines.append(header)
        lines.append("-" * min(len(header), 200))
        for row in rows[:max_rows]:
            lines.append(" | ".join(row.get(column, "") for column in columns))
        if len(rows) > max_rows:
            lines.append(f"...（共 {len(rows)} 行，仅显示前 {max_rows} 行）")
        return clip_text("\n".join(lines), self.config.max_tool_result_chars)

    def python_code_interpreter(self, arguments: Mapping[str, Any]) -> str:
        code = text_argument(arguments, "code", required=True)
        raw_filename = text_argument(arguments, "filename", default="")
        filename = (
            Path(raw_filename).name
            if raw_filename
            else f"snippet_{datetime.now():%Y%m%d_%H%M%S}.py"
        )
        if not filename.endswith(".py"):
            filename += ".py"

        sandbox = self.sandbox_dir
        script_path = sandbox / filename
        script_path.write_text(code, encoding="utf-8", newline="\n")

        timeout = max(1, int(self.config.agronomy_code_timeout))
        env = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(sandbox / ".mplconfig"),
            "HOME": str(sandbox),
            "USERPROFILE": str(sandbox),
            "TEMP": str(sandbox),
            "TMP": str(sandbox),
            "PATH": os.environ.get("PATH", ""),
        }
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")

        created_before = {item.name for item in sandbox.iterdir() if item.is_file()}
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, "-B", str(script_path)],
                cwd=str(sandbox),
                env=env,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, _ = decode_text(exc.stdout or b"", strict=False)
            stderr, _ = decode_text(exc.stderr or b"", strict=False)
            stdout = clip_text(stdout, self.config.cli_max_output_chars)
            stderr = clip_text(stderr, self.config.cli_max_output_chars)
            return (
                f"沙盒脚本：{script_path}\n"
                f"状态：超时（超过 {timeout} 秒，进程已终止）\n\n"
                f"--- stdout ---\n{stdout or '（无输出）'}\n\n"
                f"--- stderr ---\n{stderr or '（无输出）'}"
            )
        except OSError as exc:
            raise ToolError(f"启动 Python 沙盒子进程失败：{exc}") from exc

        elapsed = time.perf_counter() - started
        stdout, _ = decode_text(completed.stdout or b"", strict=False)
        stderr, _ = decode_text(completed.stderr or b"", strict=False)
        stdout = clip_text(stdout, self.config.cli_max_output_chars)
        stderr = clip_text(stderr, self.config.cli_max_output_chars)

        created_after = {
            item.name
            for item in sandbox.iterdir()
            if item.is_file()
        }
        new_files = sorted(created_after - created_before - {filename})
        lines = [
            f"沙盒脚本：{script_path}",
            f"退出码：{completed.returncode}（耗时 {elapsed:.2f}s，超时上限 {timeout}s）",
            f"工作目录：{sandbox}",
            "",
            "--- stdout ---",
            stdout or "（无输出）",
            "",
            "--- stderr ---",
            stderr or "（无输出）",
        ]
        if new_files:
            lines.append("")
            lines.append("沙盒内新增文件：" + "，".join(new_files[:20]))
        return "\n".join(lines)

    def field_trial_analysis(self, arguments: Mapping[str, Any]) -> str:
        path = text_argument(arguments, "path", required=True)
        group_column = text_argument(arguments, "group_column", required=True)
        value_column = text_argument(arguments, "value_column", required=True)
        sheet = text_argument(arguments, "sheet", default="")
        method = text_argument(arguments, "outlier_method", default="both").lower()
        if method not in ("both", "3sigma", "sigma", "iqr", "none", "off"):
            raise ToolError(
                "outlier_method 只支持 both / 3sigma / iqr / none"
            )
        alpha = float((arguments or {}).get("alpha", 0.05))
        if not 0 < alpha < 1:
            raise ToolError("alpha 必须在 0~1 之间，例如 0.05")
        plot = bool((arguments or {}).get("plot", True))

        columns, rows, sheet_name = read_table(path, sheet=sheet or None)
        group_column = _find_column(columns, group_column)
        value_column = _find_column(columns, value_column)
        groups, skipped = _group_values(rows, group_column, value_column)
        if len(groups) < 2:
            raise ToolError(
                f"分组列 {group_column!r} 只解析出 {len(groups)} 个分组；"
                "至少需要 2 个分组才能做方差分析"
            )

        outliers = _detect_outliers(groups, method)
        cleaned = _clean_groups(groups, outliers, method)
        usable = [group for group in cleaned if group.values]
        if len(usable) < 2 or sum(len(group.values) for group in usable) <= len(usable):
            raise ToolError("剔除异常值后有效观测不足，无法进行 ANOVA；请调整 outlier_method")

        anova = _anova(usable)
        lsd_rows, tukey_rows, t_crit, q_crit = _pairwise(
            usable, anova["ms_within"], anova["df_within"], alpha
        )
        letters = _letter_groups(usable, tukey_rows)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"field_trial_{timestamp}"
        output_dir = self.output_dir
        json_path = output_dir / f"{stem}.json"
        stats_csv = output_dir / f"{stem}_groups.csv"
        png_path = output_dir / f"{stem}.png"

        group_stats: list[dict[str, Any]] = []
        for group in usable:
            values = group.values
            sd = math.sqrt(_variance(values))
            group_stats.append(
                {
                    "group": group.name,
                    "n": len(values),
                    "mean": _mean(values),
                    "sd": sd,
                    "se": sd / math.sqrt(len(values)) if values else float("nan"),
                    "min": min(values) if values else float("nan"),
                    "max": max(values) if values else float("nan"),
                    "cv": (sd / _mean(values) * 100) if _mean(values) else float("nan"),
                    "letter": letters.get(group.name, ""),
                }
            )

        with stats_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["分组", "样本数", "均值", "标准差", "标准误", "最小值", "最大值", "变异系数%", "显著性字母"]
            )
            for item in group_stats:
                writer.writerow(
                    [
                        item["group"],
                        item["n"],
                        _fmt(item["mean"]),
                        _fmt(item["sd"]),
                        _fmt(item["se"]),
                        _fmt(item["min"]),
                        _fmt(item["max"]),
                        _fmt(item["cv"], 2),
                        item["letter"],
                    ]
                )

        plot_note = ""
        plot_engine = ""
        if plot:
            if importlib.util.find_spec("matplotlib"):
                try:
                    plot_engine = _draw_plot(
                        usable,
                        png_path,
                        f"{Path(path).stem}：{value_column} 分组箱线图",
                    )
                    plot_note = str(png_path)
                except Exception as exc:
                    plot_note = f"绘图失败：{exc}"
            else:
                plot_note = "未安装 matplotlib，已跳过绘图"

        summary = {
            "source": str(resolve_path(path)),
            "sheet": sheet_name,
            "group_column": group_column,
            "value_column": value_column,
            "outlier_method": method,
            "alpha": alpha,
            "skipped_rows": skipped,
            "outliers": outliers,
            "group_stats": group_stats,
            "anova": anova,
            "lsd": {"t_critical": t_crit, "comparisons": lsd_rows},
            "tukey": {"q_critical": q_crit, "comparisons": tukey_rows},
            "plots": {"png": plot_note, "engine": plot_engine},
        }
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        lines = [
            "农学田间试验统计分析",
            f"数据文件：{resolve_path(path)}（工作表：{sheet_name}）",
            f"分组列：{group_column}；观测列：{value_column}",
            f"异常值方法：{method}；显著性水平：α={alpha}",
            f"读入 {len(rows)} 行，有效分组 {len(usable)} 个，"
            f"有效观测 {sum(len(group.values) for group in usable)} 个",
        ]
        if skipped:
            lines.append(f"非数值/缺失值跳过：{len(skipped)} 行")
        lines.append("")

        lines.append("【描述统计】")
        lines.append("分组 | n | 均值 | 标准差 | 标准误 | 最小值 | 最大值 | 变异系数% | 字母")
        for item in group_stats:
            lines.append(
                f"{item['group']} | {item['n']} | {_fmt(item['mean'])} | "
                f"{_fmt(item['sd'])} | {_fmt(item['se'])} | "
                f"{_fmt(item['min'])} | {_fmt(item['max'])} | "
                f"{_fmt(item['cv'], 2)} | {item['letter']}"
            )
        lines.append("")

        lines.append("【异常值检测】")
        any_outlier = False
        for group in groups:
            entry = outliers.get(group.name, {})
            sigma = entry.get("sigma", [])
            iqr = entry.get("iqr", [])
            if not sigma and not iqr:
                continue
            any_outlier = True
            details: list[str] = []
            for item in sigma:
                details.append(f"第{item['row']}行={_fmt(item['value'])}（3σ）")
            for item in iqr:
                details.append(f"第{item['row']}行={_fmt(item['value'])}（IQR）")
            lines.append(f"{group.name}：" + "；".join(details))
        if not any_outlier:
            lines.append("未检测到异常值")
        lines.append("")

        lines.append("【单因素方差分析 ANOVA】")
        lines.append("变异来源 | SS | df | MS | F | p")
        lines.append(
            f"组间 | {_fmt(anova['ss_between'])} | {int(anova['df_between'])} | "
            f"{_fmt(anova['ms_between'])} | {_fmt(anova['f'])} | {_fmt(anova['p'], 6)}"
        )
        lines.append(
            f"组内 | {_fmt(anova['ss_within'])} | {int(anova['df_within'])} | "
            f"{_fmt(anova['ms_within'])} | - | -"
        )
        lines.append(
            f"总计 | {_fmt(anova['ss_total'])} | {int(anova['df_between'] + anova['df_within'])} | - | - | -"
        )
        significant = anova["p"] < alpha
        lines.append(
            f"结论：{'差异显著' if significant else '差异不显著'}"
            f"（F={_fmt(anova['f'])}，p={_fmt(anova['p'], 6)}，"
            f"η²={_fmt(anova['eta_squared'])}）"
        )
        lines.append("")

        lines.append(f"【LSD 多重比较】t 临界值={_fmt(t_crit)}")
        for row in lsd_rows:
            lines.append(
                f"{row['a']} vs {row['b']}：差值 {_fmt(row['diff'])}，"
                f"LSD {_fmt(row['lsd'])} → "
                f"{'显著' if row['significant'] else '不显著'}"
            )
        lines.append("")

        lines.append(f"【Tukey HSD】q 临界值≈{_fmt(q_crit)}（α={alpha}，查表插值）")
        for row in tukey_rows:
            lines.append(
                f"{row['a']} vs {row['b']}：差值 {_fmt(row['diff'])}，"
                f"q={_fmt(row['q'])} → "
                f"{'显著' if row['significant'] else '不显著'}"
            )
        lines.append("")

        lines.append("【显著性字母分组】")
        for item in group_stats:
            lines.append(f"{item['group']}：{item['letter'] or '-'}（均值 {_fmt(item['mean'])}）")
        lines.append("")

        lines.append("【本地输出文件】")
        lines.append(f"统计明细：{stats_csv}")
        lines.append(f"完整结果：{json_path}")
        if plot_note:
            lines.append(f"绘图结果：{plot_note}")

        return clip_text("\n".join(lines), self.config.max_tool_result_chars)

    def report_generator(self, arguments: Mapping[str, Any]) -> str:
        title = text_argument(arguments, "title", default="耕云田间试验分析报告")
        content = (arguments or {}).get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            raise ToolError("content 不能为空，请提供报告正文（Markdown）")

        raw_path = text_argument(arguments, "output_path", default="")
        raw_format = text_argument(arguments, "format", default="").lower()
        source = text_argument(arguments, "source", default="")

        if raw_path:
            output = Path(resolve_path(raw_path))
        else:
            suffix = ".docx" if raw_format == "docx" else ".md"
            output = self.output_dir / (
                f"report_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
            )
        fmt = raw_format or output.suffix.lstrip(".").lower() or "md"
        if fmt not in ("md", "markdown", "docx"):
            raise ToolError("format 只支持 md / markdown / docx")
        if fmt == "markdown":
            fmt = "md"
        if fmt == "docx" and output.suffix.lower() != ".docx":
            output = output.with_suffix(".docx")

        output.parent.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        appendix = ""
        if source:
            source_path = Path(resolve_path(source))
            if not source_path.is_file():
                raise ToolError(f"source 文件不存在：{source_path}")
            appendix = "\n\n## 附录：原始分析结果\n\n" + source_path.read_text(
                encoding="utf-8", errors="replace"
            )

        if fmt == "md":
            body = (
                f"# {title}\n\n"
                f"> 生成时间：{generated_at}\n"
                f"> 生成工具：耕云 xAgent ReportGenerator\n\n"
                f"{content.strip()}{appendix}\n"
            )
            output.write_text(body, encoding="utf-8", newline="\n")
        else:
            try:
                from docx import Document
                from docx.shared import Pt
            except ImportError as exc:
                raise ToolError(
                    "生成 Word 报告需要安装 python-docx：pip install python-docx"
                ) from exc

            document = Document()
            document.add_heading(title, level=0)
            document.add_paragraph(f"生成时间：{generated_at}")
            document.add_paragraph("生成工具：耕云 xAgent ReportGenerator")
            for line in (content.strip() + appendix).splitlines():
                text = line.strip()
                if not text:
                    continue
                if text.startswith("#"):
                    level = min(4, max(1, len(text) - len(text.lstrip("#"))))
                    document.add_heading(text.lstrip("#").strip(), level=level)
                elif text.startswith(("- ", "* ")):
                    paragraph = document.add_paragraph(text[2:], style="List Bullet")
                    paragraph.style.font.size = Pt(10.5)
                else:
                    document.add_paragraph(text)
            document.save(str(output))

        size = output.stat().st_size if output.exists() else 0
        return (
            f"报告已生成：{output}\n"
            f"格式：{fmt}；大小：{human_size(size)}；生成时间：{generated_at}"
        )

    # ------------------------------------------------------------- 工具声明
    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="LocalFileReader",
                description=(
                    "读取本地农学试验数据文件（CSV/TSV/Excel），返回工作表、"
                    "列名、行数和前 N 行预览，适合 reads.xlsx / 试验数据.csv"
                ),
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "数据文件路径（.csv / .tsv / .xlsx / .xlsm / .xls）",
                        },
                        "sheet": {
                            "type": "string",
                            "description": "Excel 工作表名称或从 0 开始的索引，默认第一个工作表",
                        },
                        "max_rows": {
                            "type": "integer",
                            "description": "预览行数，默认 30，最大 500",
                        },
                    },
                    required=["path"],
                ),
                handler=self.local_file_reader,
                example_arguments={"path": "data/field_trial.csv", "max_rows": 20},
            ),
            ToolSpec(
                name="PythonCodeInterpreter",
                description=(
                    "在项目内隔离目录中执行 Python 代码（本地子进程、限定工作目录、"
                    "超时终止、输出截断），适合 pandas/numpy 数据清洗与自定义统计"
                ),
                parameters=object_schema(
                    properties={
                        "code": {
                            "type": "string",
                            "description": "要执行的 Python 代码；工作目录为 .agents/sandbox",
                        },
                        "filename": {
                            "type": "string",
                            "description": "可选脚本文件名（只取文件名，不允许路径），例如 clean.py",
                        },
                    },
                    required=["code"],
                ),
                handler=self.python_code_interpreter,
                example_arguments={"code": "print('hello agronomy')"},
            ),
            ToolSpec(
                name="FieldTrialAnalysis",
                description=(
                    "农学田间试验统计分析：按分组列自动分组，做 3σ/IQR 异常值检测、"
                    "单因素 ANOVA、LSD 与 Tukey HSD 多重比较、显著性字母分组，"
                    "并在本地保存统计 CSV、JSON 和箱线图 PNG"
                ),
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "试验数据文件路径（CSV/TSV/Excel）",
                        },
                        "group_column": {
                            "type": "string",
                            "description": "分组列名，例如 品种 / 处理 / 施肥水平",
                        },
                        "value_column": {
                            "type": "string",
                            "description": "观测值列名，例如 产量 / 株高 / 千粒重",
                        },
                        "sheet": {
                            "type": "string",
                            "description": "Excel 工作表名称或索引，默认第一个",
                        },
                        "outlier_method": {
                            "type": "string",
                            "enum": ["both", "3sigma", "iqr", "none"],
                            "description": "异常值检测方法，默认 both（3σ 与 IQR 取并集）",
                        },
                        "alpha": {
                            "type": "number",
                            "description": "显著性水平，默认 0.05",
                        },
                        "plot": {
                            "type": "boolean",
                            "description": "是否绘制分组箱线图并保存 PNG，默认 true",
                        },
                    },
                    required=["path", "group_column", "value_column"],
                ),
                handler=self.field_trial_analysis,
                example_arguments={
                    "path": "data/field_trial.csv",
                    "group_column": "品种",
                    "value_column": "产量",
                    "outlier_method": "both",
                    "alpha": 0.05,
                    "plot": True,
                },
            ),
            ToolSpec(
                name="ReportGenerator",
                description=(
                    "把分析结论生成为 Markdown（可选 Word .docx）报告并保存到本地，"
                    "支持附加原始分析结果文件"
                ),
                parameters=object_schema(
                    properties={
                        "title": {
                            "type": "string",
                            "description": "报告标题",
                        },
                        "content": {
                            "type": "string",
                            "description": "报告正文，支持 Markdown 标题、列表和段落",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "可选输出路径，默认保存到 .agents/outputs/",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["md", "docx"],
                            "description": "输出格式，默认 md",
                        },
                        "source": {
                            "type": "string",
                            "description": "可选，附加到报告末尾的原始数据/分析结果文件路径",
                        },
                    },
                    required=["title", "content"],
                ),
                handler=self.report_generator,
                example_arguments={
                    "title": "水稻品种比较试验分析报告",
                    "content": "## 试验设计\n随机区组设计...\n\n## 结论\n品种 A 产量显著高于对照。",
                    "format": "md",
                },
            ),
        ]


__all__ = [
    "AgronomyToolbox",
    "read_table",
]
