"""Numerical reports and honest PPI comparisons. Plotting is an optional dependency."""
from __future__ import annotations
import csv
import gc
import gzip
import io
from pathlib import Path
import numpy as np
from .engine import Reason
from .io import clean


def statistics(case, d, evidence, outcome):
    roi = d['review_roi'].astype(bool)
    visible = d['baseline_business_visible'].astype(bool)
    a = evidence.arrays
    state = a['state']
    return {
        "case": case, "roi_semantics": "review_only_not_truth",
        "roi_gates": int(roi.sum()), "visible_before": int(visible.sum()),
        "roi_reference_fit": int((roi & (a['source_fit_family']>0)).sum()),
        "roi_family_noisy": int((roi & (a['family_code']==1)).sum()),
        "roi_family_coherent": int((roi & (a['family_code']==2)).sum()),
        "roi_weather_conflict": int((roi & (state==4)).sum()),
        "roi_source_supported": int((roi & (state==5)).sum()),
        "roi_policy_proposal": int((roi & outcome.proposed_quarantine).sum()),
        "roi_added_quarantine": int((roi & outcome.added_quarantine).sum()),
        "roi_still_visible": int((roi & outcome.arrays['business_visible']).sum()),
        "outside_roi_visible_additions": int((~roi & visible & outcome.added_quarantine).sum()),
        "visible_loss": int((visible & outcome.added_quarantine).sum()),
        "confirmed_additions": 0,
        "state_counts_roi": {str(i): int(np.sum(roi & (state==i))) for i in range(7)},
        "reason_counts_roi": {reason.name: int(np.sum(roi & ((a['reason'] & int(reason))!=0))) for reason in Reason},
        "outcome": outcome.summary,
        "precision": None, "recall": None, "true_weather_loss": None,
        "performance": evidence.timing,
        "operational_eligible": False,
    }


def gate_rows(raw, d, evidence, outcome, mask):
    a = evidence.arrays
    for ray, gate in zip(*np.where(mask), strict=True):
        row = {'ray': int(ray), 'gate': int(gate), 'range_m': float(raw.ranges[gate]),
               'azimuth_rotated_deg': float(raw.azimuth[ray]), 'observed': bool(raw.observed[ray, gate]),
               'review_roi': bool(d['review_roi'][ray, gate]), 'truth_label': None}
        for name in raw.fields:
            row['raw_'+name] = clean(raw.fields[name][ray, gate])
            row['available_'+name] = bool(raw.available[name][ray, gate])
        for name, arr in a.items():
            row[name] = clean(arr[ray, gate])
        row['reason_names'] = '|'.join(bit.name for bit in Reason if a['reason'][ray, gate] & int(bit))
        for key in ('QC_ACTION','RFI_QUARANTINE_MASK','QPE_ELIGIBLE_MASK','QUALITY_INDEX','business_visible'):
            row['before_'+key] = clean(d['baseline_'+key][ray, gate])
            row['after_'+key] = clean(outcome.arrays[key][ray, gate])
        row['confirmed_addition'] = False
        row['added_quarantine'] = bool(outcome.added_quarantine[ray, gate])
        yield row


def write_csv(path, rows, compress=False):
    rows = iter(rows)
    first = next(rows, None)
    if compress:
        with Path(path).open('wb') as f:
            with gzip.GzipFile(filename='', fileobj=f, mode='wb', mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding='utf-8', newline='') as text:
                    _csv(text, first, rows)
    else:
        with Path(path).open('w', encoding='utf-8', newline='') as f:
            _csv(f, first, rows)


def _csv(f, first, rows):
    if first is None:
        return
    writer = csv.DictWriter(f, fieldnames=list(first))
    writer.writeheader()
    writer.writerow(first)
    writer.writerows(rows)


def plots(path, case, raw, baseline, outcome, evidence):
    # Same numerical renderer/extent for all views; not compared pixelwise to
    # operational renderer 1.2.0. Explicitly not a smoothed/cleaned image.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    path = Path(path)
    path.mkdir()
    theta = np.deg2rad(raw.azimuth)[:,None]
    r = raw.ranges[None,:]/1000
    x, y = r*np.sin(theta), r*np.cos(theta)
    extent = float(raw.ranges[-1]/1000)
    fields = {
        'raw': (raw.fields['DBZH'], raw.observed, 'Original measured reflectivity'),
        'baseline': (baseline['DBZH_QC'], baseline['business_visible'], 'Frozen baseline business reflectivity'),
        'experiment': (outcome.arrays['DBZH_QC'], outcome.arrays['business_visible'],
                       'Experimental quarantine applied - NOT confirmed RFI'),
    }
    for name, (values, valid, title) in fields.items():
        fig, ax = plt.subplots(figsize=(6.4,6.4), dpi=100)
        m = valid.astype(bool) & np.isfinite(values) & (values >= -10)
        image = ax.scatter(x[m], y[m], c=values[m], s=0.5, vmin=-10, vmax=70, rasterized=True)
        ax.set(xlim=(-extent,extent), ylim=(-extent,extent), aspect='equal', xlabel='Rotated x (km)', ylabel='Rotated y (km)')
        ax.set_title(f'{case}: {title}', fontsize=10)
        fig.colorbar(image, ax=ax, label='dBZ', shrink=.65)
        fig.tight_layout()
        fig.savefig(path/(name+'.png'))
        plt.close(fig)
        del fig, ax, image
        gc.collect()
    fig, ax = plt.subplots(figsize=(6.4,6.4), dpi=100)
    m = raw.observed
    h = ax.scatter(x[m], y[m], c=evidence.arrays['state'][m], s=.5, vmin=0, vmax=6, rasterized=True)
    ax.set(xlim=(-extent,extent), ylim=(-extent,extent), aspect='equal', xlabel='Rotated x (km)', ylabel='Rotated y (km)')
    ax.set_title(f'{case}: competing-hypothesis states (not truth)', fontsize=10)
    fig.colorbar(h, ax=ax, label='State code', shrink=.65)
    fig.tight_layout(); fig.savefig(path/'states.png'); plt.close(fig)
    del fig, ax, h
    gc.collect()


def html_index(root, summaries):
    root = Path(root)
    rows = []
    for s in summaries:
        c = s['case']
        rows.append(f'<tr><td>{c}</td><td>{s["roi_gates"]}</td><td>{s["roi_source_supported"]}</td><td>{s["roi_added_quarantine"]}</td><td>{s["outside_roi_visible_additions"]}</td><td>0</td></tr>')
    sections = []
    for s in summaries:
        c = s['case']
        pics = ''.join(f'<figure><img src="{c}/figures/{n}.png" alt="{n}"><figcaption>{label}</figcaption></figure>' for n,label in (
            ('raw','原始实测'),('baseline','冻结基线'),('experiment','实际实验隔离；非确认污染')))
        sections.append(f'<section><h2>{c}</h2><p><a href="{c}/summary.json">逐门统计</a> · <a href="{c}/roi_gates.csv">ROI逐门证据</a> · <a href="{c}/folds.json.gz">目标外参考区间</a></p><div class="row">{pics}</div></section>')
    text = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>RainPulse OC1 五例</title>
<style>body{font:16px/1.65 system-ui;max-width:1320px;margin:32px auto;padding:0 20px;color:#203432}table{border-collapse:collapse;width:100%}th,td{padding:8px;border-bottom:1px solid #ccd8d6;text-align:left}.row{display:flex;flex-wrap:wrap}figure{margin:0;width:33.33%}img{width:100%}h2{margin-top:40px}.notice{padding:16px;background:#fff3d6}</style>
<h1>OC1 原始对象跨块建模：实际离线实验结果</h1>
<p class="notice">第三列确实应用了实验隔离，不是只画提议。但这不是确认污染/准确率改善。原始数据未修改；ROI不是标签；图中空白不是有效无雨。真实天气误隔离尚未独立验证。</p>
<p>同一绘图器 oc1-scatter-v1、固定 -10~70 dBZ、同范围；不是操作 renderer 1.2.0，不作跨渲染器像素差计分。baseline 和 experiment 使用冻结资格掩码，不插值/不平滑。审计模式另有逐数组不变测试。</p>
<table><tr><th>案例</th><th>ROI门</th><th>源假设暂占优</th><th>ROI实际新增隔离</th><th>ROI外可见门新增隔离</th><th>新确认污染</th></tr>'''+''.join(rows)+ '</table>'+''.join(sections)+'''<h2>局限</h2><p>没有独立真值、真实时间、全体积和可信地理配准。不能用源模型相容代替污染真值。源与天气统计完全相同的输入不可区分。本实验没有新增学习权重或信号分解订正。</p></html>'''
    (root/'index.html').write_text(text, encoding='utf-8')
