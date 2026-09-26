import numpy as np
import pytest
from rainpulse_algo import performance as perf
from rainpulse_algo.multiband import selection_kernel as k
from rainpulse_algo.multiband.selection_warmup import STATE_DTYPE


def test_numpy_warmup_no_numba():
    assert k.warmup('numpy') is None
    with pytest.raises(ValueError):k.warmup('gpu')


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('layout',['contiguous','structured','memmap'])
def test_warmup_real_layouts(dtype,layout,tmp_path):
    pytest.importorskip('numba')
    with perf.capture_traces(lambda _:None):k.warmup('numba')
    count=len(k.require_numba().signatures)
    shape=(8,13);rng=np.random.default_rng(223)
    b=rng.random(shape)>.2;f=rng.random(shape);z=rng.uniform(-20,70,shape).astype(dtype)
    i=rng.integers(0,20,shape,dtype=np.int64)
    if layout=='contiguous':
        state=(np.full(shape,-np.inf),np.full(shape,np.nan,'f4'),
               *[np.full(shape,-1,'i4') for _ in range(3)],*[np.full(shape,np.inf,'f4') for _ in range(3)])
    else:
        record=(np.zeros(shape,dtype=STATE_DTYPE) if layout=='structured' else
                np.memmap(tmp_path/'test.bin',mode='w+',dtype=STATE_DTYPE,shape=shape))
        state=tuple(record[n] for n in STATE_DTYPE.names);state[0][:]=-np.inf
    old=tuple(a.copy() for a in state)
    args=(b,f,~b,z,i,i,f,f,f,0)
    for step in range(3):
        # Repeat to exercise ties, nan values, age and resolution priorities.
        k.select_winners(*args,*old,backend='numpy')
        k.select_winners(*args,*state,backend='numba')
        for a,c in zip(old,state):np.testing.assert_array_equal(a,c)
    assert len(k.require_numba().signatures)==count
    if layout=='memmap':record._mmap.close()


def test_observes_new_layout_compile():
    pytest.importorskip('numba');reports=[]
    with perf.capture_traces(reports.append):k.warmup('numba')
    assert reports and reports[0]['counters']['numba.warmup_signatures_after']>=4
