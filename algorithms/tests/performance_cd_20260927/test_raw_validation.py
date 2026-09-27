from types import SimpleNamespace as NS
import hashlib,json
import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.raw_reuse import (
    immutable_array,shared_native_scope,shared_native_view,array_digest)
from rainpulse_algo.radar.qc_engine.validation_reads import ValidationReadGroup,shared_validation_reads


def legacy_digest(arrays):
    h=hashlib.sha256()
    for key in sorted(arrays):
        a=np.asarray(arrays[key]);a=np.array(a,dtype=a.dtype.newbyteorder('<'),order='C',copy=True)
        if a.dtype.kind in 'fc':a[np.isnan(a)]=np.nan
        header=json.dumps([key,a.dtype.str,list(a.shape)],sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
        h.update(len(header).to_bytes(8,'little'));h.update(header);h.update(a.tobytes())
    return h.hexdigest()

@pytest.mark.parametrize('dtype',['float16','float32','float64','>f4','complex64','int64','uint32','bool','datetime64[ns]','S4','U4'])
@pytest.mark.parametrize('layout',['c','f','strided','reverse','scalar','empty'])
def test_exact_block_hash(dtype,layout):
    a=np.arange(48).reshape(6,8).astype(dtype)
    if np.dtype(dtype).kind in 'fc':a[2,2]=np.nan;a[3,4]=-0.
    a={'c':lambda:a,'f':lambda:np.asfortranarray(a),'strided':lambda:a[::2,1::2],
       'reverse':lambda:a[::-1,::-1],'scalar':lambda:a[0,0],'empty':lambda:a[:0]}[layout]()
    values={'数值':a,'mask':np.zeros((2,3),bool)}
    assert array_digest(values,block_bytes=17)==legacy_digest(values)


def native():
    return NS(fields={'DBZH':np.arange(40,dtype='float32').reshape(5,8)},
              field_available={'DBZH':np.ones((5,8),bool)},azimuth=np.arange(5.),
              elevation=np.ones(5),ranges=np.arange(8.),geometry_good=np.ones(5,bool),
              gap_after=np.zeros(5,bool),ray_time=np.arange(5.),original_indices=np.arange(5))


def test_owner_snapshot_readonly_and_cross_policy_sharing():
    n=native()
    @shared_native_view('a')
    def factory(x):return immutable_array(x.fields['DBZH'])
    @shared_native_view('b')
    def other(x):return immutable_array(x.fields['DBZH'])
    @shared_native_scope
    def run(result,ns):
        a=factory(ns[0]);b=factory(ns[0]);c=other(ns[0])
        assert a is b and np.shares_memory(a,c)
        with pytest.raises(ValueError):a.setflags(write=True)
        with pytest.raises(ValueError):a[0,0]=4
        return a
    first=run(None,[n]);n.fields['DBZH'][0,0]=123
    second=run(None,[n]);assert first[0,0]==0 and second[0,0]==123


def test_owner_mutation_is_detected_and_exception_context_released():
    n=native()
    @shared_native_scope
    def bad(_,ns):
        immutable_array(ns[0].fields['DBZH']);ns[0].fields['DBZH'][0,0]=99
    with pytest.raises(RuntimeError,match='owner changed'):bad(None,[n])
    b=immutable_array(n.fields['DBZH']);assert b[0,0]==99


def test_array_mutable_owner_not_shared_and_dtype():
    a=np.arange(7,dtype='float32');v=a.view();v.flags.writeable=False
    b=immutable_array(v);a[0]=123;assert b[0]==0
    assert immutable_array(a,'float64').dtype==np.float64
    with pytest.raises(ValueError):immutable_array(np.array([{}],object))


class Field:
    def __init__(self,a):self.a=a;self.shape=a.shape;self.dtype=a.dtype;self.reads=0
    def __getitem__(self,k):self.reads+=1;return self.a[k].copy()


def test_validation_reads_whole_alias_and_eviction():
    g={'v':Field(np.arange(10)),'before':Field(np.arange(10)+2)}
    with_view=ValidationReadGroup(g,maximum_bytes=80)
    a=with_view['v'][:];b=with_view['v'][:];assert a is b and g['v'].reads==1
    with_view['before'][:];with_view['v'][:];assert g['v'].reads==2
    assert with_view.peak<=80 and with_view.evictions==2
    with pytest.raises(ValueError):b.setflags(write=True)
    with_view.close()
    with pytest.raises(RuntimeError):with_view['v']


def test_recursive_validation_keeps_checks_and_scope():
    g={'v':Field(np.arange(8))};seen=[]
    @shared_validation_reads
    def validate(group,level):
        x=group['v'][:];seen.append(x)
        if level:validate(group,level-1)
        else:
            assert np.array_equal(x,np.arange(8))
    validate(g,3);assert g['v'].reads==1 and all(a is seen[0] for a in seen)
    g['v'].a[0]=88
    with pytest.raises(AssertionError):validate(g,1)
    assert g['v'].reads==2


def test_validation_zero_budget_and_partial_read():
    g={'v':Field(np.arange(8))};v=ValidationReadGroup(g,0)
    v['v'][:];v['v'][:];assert g['v'].reads==2 and v.bytes==0
    np.testing.assert_equal(v['v'][2:4],np.array([2,3]));assert g['v'].reads==3
    v.close()


def test_actual_zarr2_validation_reader():
    zarr=pytest.importorskip('zarr',reason='optional real Zarr 2 integration')
    if int(zarr.__version__.split('.')[0])!=2:
        pytest.skip('production contract uses Zarr 2')
    root=zarr.group(store=zarr.storage.MemoryStore())
    root.create_dataset('v',data=np.arange(40).reshape(5,8),chunks=(2,4))
    v=ValidationReadGroup(root)
    np.testing.assert_equal(v['v'][:],root['v'][:]);v['v'][:]
    assert v.hits==1 and v.reads==1
    v.close()
