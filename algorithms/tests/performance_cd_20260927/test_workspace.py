from types import SimpleNamespace as NS
from pathlib import Path
import numpy as np
import pytest
from rainpulse_algo.multiband.workspace_pool import WorkspacePool, DTYPE, FIELDS, workspace_plan


def settings(memory=10**6):
    return NS(layer_memory_bytes=memory, maximum_tile_bytes=100000,
              maximum_scratch_bytes=10**7)

def grid():
    return NS(width=7, height=11, tile_rows=3, levels_m_msl=(1., 2.))

@pytest.mark.parametrize('capacity', [0, 1, 1512, 3024, 7000, 1000000])
@pytest.mark.parametrize('comparison', [False, True])
def test_roundtrip_budget_and_no_cross_workspace_alias(tmp_path, capacity, comparison):
    g=grid();p=WorkspacePool(g,settings(capacity),tmp_path,comparison=comparison)
    names=('SX','S','X') if comparison else ('SX',)
    expected={};state_size=g.width*g.height*len(g.levels_m_msl)*DTYPE.itemsize
    for epoch in range(3):
        for wi,name in enumerate(names):
            for row in range(0,g.height,g.tile_rows):
                stop=min(row+g.tile_rows,g.height)
                with p.workspace(name).tile(row,stop) as values:
                    if epoch==0:
                        assert np.isneginf(values[0]).all()
                        assert (values[2]==-1).all()
                        values[0][:]=wi*100+row
                    else:
                        np.testing.assert_array_equal(values[0],expected[(name,row)])
                    values[0][:]+=1
                    values[1][:]=np.float32(wi+row+epoch*.25)
                    values[2][:]=wi
                    expected[(name,row)]=values[0].copy()
    for name in names:
        for row in range(0,g.height,g.tile_rows):
            with p.workspace(name).tile(row,min(row+g.tile_rows,g.height),write=False) as v:
                np.testing.assert_array_equal(v[0],expected[name,row])
                with pytest.raises(ValueError):v[0][...]=5
    metrics=p.metrics()
    assert metrics['height_resident_array_peak_bytes']<=max(capacity,p.plan['tile_bytes'])
    assert metrics['height_state_bytes']==state_size*len(names)
    assert metrics['layer_file_write_bytes']==sum(metrics[f'layer_{n.lower()}_file_write_bytes'] for n in names)
    if capacity >= state_size*len(names):
        assert metrics['layer_file_write_bytes']==metrics['layer_file_read_bytes']==0
        assert metrics['layer_scratch_bytes']==0
    else:assert metrics['layer_file_writes']>0
    directory=p.directory;p.close();assert not directory.exists();assert not list(tmp_path.iterdir())


def test_dirty_resident_reused_without_intermediate_flush(tmp_path):
    g=grid();p=WorkspacePool(g,settings(3024),tmp_path,comparison=True)
    for _ in range(4):
        with p.workspace('SX').tile(0,3) as v:v[2][:]=2
        with p.workspace('X').tile(0,3) as v:v[2][:]=3
    assert p.metrics()['layer_file_writes']==0
    with p.workspace('S').tile(0,3):pass
    assert p.metrics()['layer_file_writes']==1
    assert p.metrics()['layer_tile_hits']==6
    p.close()


def test_spill_reservation_and_shortage(tmp_path, monkeypatch):
    g=grid();o=settings(0);p=workspace_plan(g,o,comparison=True)
    assert p['disk_reservation']==3*p['state_bytes']+p['tile_bytes']
    o.maximum_scratch_bytes=p['disk_reservation']-1
    with pytest.raises(ValueError,match='scratch'):workspace_plan(g,o,comparison=True)
    monkeypatch.setattr('rainpulse_algo.multiband.workspace_pool.shutil.disk_usage',lambda _:NS(free=0))
    with pytest.raises(ValueError,match='space'):WorkspacePool(g,settings(0),tmp_path)
    assert not list(tmp_path.iterdir())


def test_exception_does_not_write_partial_active_tile(tmp_path):
    with WorkspacePool(grid(),settings(0),tmp_path) as p:
        with pytest.raises(RuntimeError,match='injected'):
            with p.workspace('SX').tile(0,3) as a:
                a[0][:]=17
                raise RuntimeError('injected')
        assert p.failed and p.metrics()['layer_file_writes']==0
        with pytest.raises(RuntimeError):
            with p.workspace('SX').tile(3,6):pass
    assert not list(tmp_path.iterdir())


def test_nested_borrow_and_bad_rows(tmp_path):
    with WorkspacePool(grid(),settings(),tmp_path) as p:
        with p.workspace('SX').tile(0,3):
            with pytest.raises(RuntimeError):
                with p.workspace('SX').tile(3,6):pass
        for r,e in [(-1,2),(1,4),(0,2),(12,11)]:
            with pytest.raises(ValueError):
                with p.workspace('SX').tile(r,e):pass


def test_truncated_scratch_refused(tmp_path):
    with WorkspacePool(grid(),settings(0),tmp_path) as p:
        with p.workspace('SX').tile(0,3) as a:a[0][:]=4
        next(iter(p._files.values())).write_bytes(b'bad')
        with pytest.raises(RuntimeError,match='length'):
            with p.workspace('SX').tile(0,3):pass
    assert not list(tmp_path.iterdir())


def test_write_failure_cleans_temp(tmp_path,monkeypatch):
    with WorkspacePool(grid(),settings(0),tmp_path) as p:
        def fail(*args):raise OSError('injected rename failure')
        monkeypatch.setattr('rainpulse_algo.multiband.workspace_pool.os.replace',fail)
        with pytest.raises(OSError):
            with p.workspace('SX').tile(0,3) as a:a[0][:]=1
        assert p.failed
    assert not list(tmp_path.iterdir())


def test_complete_workspace_metrics_observed_without_changing_results(tmp_path,monkeypatch):
    from rainpulse_algo.multiband.workspace_pool import report_workspace_metrics
    captured={}
    monkeypatch.setattr('rainpulse_algo.performance.observe',lambda key,value,**kw:captured.update({key:value}))
    with WorkspacePool(grid(),settings(0),tmp_path,comparison=True) as p:
        for name in ('SX','S','X'):
            with p.workspace(name).tile(0,3) as a:a[0][:]=3
        report_workspace_metrics(p.metrics())
        for name in ('sx','s','x'):assert captured['cd.layer_'+name+'_file_write_bytes']>0
        assert captured['cd.layer_file_write_bytes']==sum(captured['cd.layer_'+n+'_file_write_bytes'] for n in ('sx','s','x'))
    monkeypatch.setattr('rainpulse_algo.performance.observe',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('logging failed')))
    report_workspace_metrics({'layer_file_write_bytes':12})
