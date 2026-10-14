"""Experimental bit-exact embedding Gather via the CUDA Driver API.

No toolkit, floating-point arithmetic, framework CPU fallback or model training.
The driver JITs this fixed PTX kernel; network peers cannot supply executable code.
"""
import ctypes as C
import hashlib
import time

import numpy as np

PTX = b"""
.version 7.0
.target sm_75
.address_size 64
.visible .entry gather_bits(
    .param .u64 table, .param .u64 ids, .param .u64 output,
    .param .u32 width, .param .u32 elements)
{
    .reg .pred p;
    .reg .b32 r<12>;
    .reg .b64 d<10>;
    ld.param.u64 d0, [table];
    ld.param.u64 d1, [ids];
    ld.param.u64 d2, [output];
    ld.param.u32 r0, [width];
    ld.param.u32 r1, [elements];
    mov.u32 r2, %ctaid.x;
    mov.u32 r3, %ntid.x;
    mov.u32 r4, %tid.x;
    mad.lo.u32 r5, r2, r3, r4;
    setp.ge.u32 p, r5, r1;
    @p bra done;
    div.u32 r6, r5, r0;
    rem.u32 r7, r5, r0;
    mul.wide.u32 d3, r6, 4;
    add.u64 d4, d1, d3;
    ld.global.u32 r8, [d4];
    mad.lo.u32 r9, r8, r0, r7;
    mul.wide.u32 d5, r9, 4;
    add.u64 d6, d0, d5;
    ld.global.u32 r10, [d6];
    mul.wide.u32 d7, r5, 4;
    add.u64 d8, d2, d7;
    st.global.u32 [d8], r10;
done:
    ret;
}
"""


class CudaGather:
    """One resident table, bounded reusable chunk buffers, synchronous host results."""
    def __init__(self, matrix, device=0):
        self.lib = C.CDLL('libcuda.so.1')
        self.allocations = []
        self.events = []
        self.context = C.c_void_p()
        self.module = C.c_void_p()
        self.device = C.c_int(device)
        self.calls = 0
        self.kernel_ms = 0.0
        self.roundtrip_seconds = 0.0
        self.shape = tuple(matrix.shape)
        if (matrix.dtype != np.dtype('<f4') or matrix.ndim != 2
                or not matrix.flags.c_contiguous or matrix.size >= 2**32
                or not 1 <= matrix.nbytes <= 4 * 1024**3):
            raise ValueError('CUDA table exceeds fixed kernel contract')
        self.width = self.shape[1]
        self.max_tokens = min(16384, (64 * 1024**2) // (4 * self.width))
        if self.max_tokens < 1:
            raise ValueError('table row exceeds bounded GPU output buffer')
        try:
            self.call('cuInit', [C.c_uint], 0)
            self.call('cuDeviceGet', [C.POINTER(C.c_int), C.c_int], C.byref(self.device), device)
            name = C.create_string_buffer(256)
            self.call('cuDeviceGetName', [C.c_void_p, C.c_int, C.c_int], name, len(name), self.device)
            driver = C.c_int()
            self.call('cuDriverGetVersion', [C.POINTER(C.c_int)], C.byref(driver))
            self.call('cuDevicePrimaryCtxRetain', [C.POINTER(C.c_void_p), C.c_int], C.byref(self.context), self.device)
            self.call('cuCtxSetCurrent', [C.c_void_p], self.context)
            free, total = C.c_size_t(), C.c_size_t()
            self.call('cuMemGetInfo_v2', [C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)],
                      C.byref(free), C.byref(total))
            required = matrix.nbytes + self.max_tokens * (4 + 4 * self.width)
            if required + 128 * 1024**2 > free.value:
                raise ValueError('insufficient GPU memory for bounded table and buffers')
            self.call('cuModuleLoadData', [C.POINTER(C.c_void_p), C.c_void_p], C.byref(self.module), PTX)
            self.function = C.c_void_p()
            self.call('cuModuleGetFunction', [C.POINTER(C.c_void_p), C.c_void_p, C.c_char_p],
                      C.byref(self.function), self.module, b'gather_bits')
            self.table = self.allocate(matrix.nbytes)
            self.ids = self.allocate(self.max_tokens * 4)
            self.output = self.allocate(self.max_tokens * self.width * 4)
            self.call('cuMemcpyHtoD_v2', [C.c_uint64, C.c_void_p, C.c_size_t], self.table, matrix.ctypes.data, matrix.nbytes)
            for _ in range(2):
                event = C.c_void_p()
                self.call('cuEventCreate', [C.POINTER(C.c_void_p), C.c_uint], C.byref(event), 0)
                self.events.append(event)
            self.runtime = dict(provider='cuda-driver-gather', device=name.value.decode(), device_ordinal=device,
                driver_api_version=driver.value, kernel_sha256=hashlib.sha256(PTX).hexdigest(),
                cpu_fallback=False, device_allocation_bytes=matrix.nbytes + self.max_tokens * (4 + 4 * self.width),
                max_tokens_per_chunk=self.max_tokens)
        except BaseException:
            self.close()
            raise

    def call(self, name, types, *args):
        function = getattr(self.lib, name)
        function.argtypes, function.restype = types, C.c_int
        result = function(*args)
        if result:
            raise RuntimeError(f'{name} failed with CUDA error {result}')

    def allocate(self, size):
        pointer = C.c_uint64()
        self.call('cuMemAlloc_v2', [C.POINTER(C.c_uint64), C.c_size_t], C.byref(pointer), size)
        self.allocations.append(pointer)
        return pointer

    def payload(self, ids):
        values = np.asarray(ids)
        if (values.ndim != 1 or values.dtype.kind not in 'ui' or not 1 <= values.size <= self.max_tokens
                or int(values.min()) < 0 or int(values.max()) >= self.shape[0]):
            raise ValueError('invalid bounded GPU token indices')
        values = np.ascontiguousarray(values, dtype='<u4')
        output = np.empty((values.size, self.width), dtype='<f4')
        started = time.perf_counter()
        self.call('cuMemcpyHtoD_v2', [C.c_uint64, C.c_void_p, C.c_size_t], self.ids, values.ctypes.data, values.nbytes)
        width, count = C.c_uint(self.width), C.c_uint(output.size)
        params = (C.c_void_p * 5)(*[C.cast(C.pointer(v), C.c_void_p) for v in
                                 (self.table, self.ids, self.output, width, count)])
        self.call('cuEventRecord', [C.c_void_p, C.c_void_p], self.events[0], None)
        self.call('cuLaunchKernel', [C.c_void_p, *([C.c_uint] * 7), C.c_void_p, C.c_void_p, C.c_void_p],
                  self.function, (output.size + 255) // 256, 1, 1, 256, 1, 1, 0, None, params, None)
        self.call('cuEventRecord', [C.c_void_p, C.c_void_p], self.events[1], None)
        self.call('cuEventSynchronize', [C.c_void_p], self.events[1])
        elapsed = C.c_float()
        self.call('cuEventElapsedTime', [C.POINTER(C.c_float), C.c_void_p, C.c_void_p],
                  C.byref(elapsed), self.events[0], self.events[1])
        self.call('cuMemcpyDtoH_v2', [C.c_void_p, C.c_uint64, C.c_size_t], output.ctypes.data, self.output, output.nbytes)
        self.calls += 1
        self.kernel_ms += elapsed.value
        self.roundtrip_seconds += time.perf_counter() - started
        return output.tobytes(order='C')

    def close(self):
        # Preserve the original failure if initialization only partially succeeded.
        for event in self.events:
            try: self.call('cuEventDestroy_v2', [C.c_void_p], event)
            except RuntimeError: pass
        self.events.clear()
        for pointer in self.allocations:
            try: self.call('cuMemFree_v2', [C.c_uint64], pointer)
            except RuntimeError: pass
        self.allocations.clear()
        if self.module.value:
            self.call('cuModuleUnload', [C.c_void_p], self.module)
            self.module = C.c_void_p()
        if self.context.value:
            self.call('cuDevicePrimaryCtxRelease_v2', [C.c_int], self.device)
            self.context = C.c_void_p()
