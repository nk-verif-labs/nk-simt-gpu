import os
import sys
import zlib
import struct
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "driver")))
from nk_gpu_asm import encode


def save_png_rgb(filename: str, width: int, height: int, pixels: list[list[tuple[int, int, int]]]):
    """Pure-Python PNG writer (no Pillow dependency required)."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            r, g, b = pixels[y][x]
            raw.extend([r & 0xFF, g & 0xFF, b & 0xFF])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    with open(filename, "wb") as f:
        f.write(png)


async def reset_dut(dut):
    dut.rst_n.value = 0
    dut.host_sel.value = 0
    dut.host_addr.value = 0
    dut.host_wdata.value = 0
    dut.host_we.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def mmio_write(dut, sel: int, addr: int, data: int):
    dut.host_sel.value = sel
    dut.host_addr.value = addr
    dut.host_wdata.value = data & 0xFFFFFFFF
    dut.host_we.value = 1
    await RisingEdge(dut.clk)
    dut.host_we.value = 0


async def mmio_read(dut, sel: int, addr: int) -> int:
    dut.host_sel.value = sel
    dut.host_addr.value = addr
    dut.host_we.value = 0
    await RisingEdge(dut.clk)
    return int(dut.host_rdata.value)


async def launch_warp(dut, warp_id: int, timeout: int = 200):
    await mmio_write(dut, 0, 0, ((warp_id & 0xFFFF) << 16) | 0x1)
    for _ in range(timeout):
        st = await mmio_read(dut, 0, 0)
        if st & 0x2:  # gpu_done
            return
    raise AssertionError(f"Warp {warp_id} timed out")


@cocotb.test()
async def test_simt_vector_mac_kernel(dut):
    """Run a multi-warp SIMT kernel: C[i] = A[i] * B[i] + D[i] across 4 warps (16 threads)."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    # Host populates A[0..15] at DMEM[0..15], B at [32..47], D at [64..79]
    A = [random.randint(1, 200) for _ in range(16)]
    B = [random.randint(1, 200) for _ in range(16)]
    D = [random.randint(0, 500) for _ in range(16)]
    for i in range(16):
        await mmio_write(dut, 2, i, A[i])
        await mmio_write(dut, 2, 32 + i, B[i])
        await mmio_write(dut, 2, 64 + i, D[i])

    # Kernel:
    #   R3 = (WID << 2) + TID   (global thread idx 0..15)
    #   R4 = DMEM[R3 + 0]       (A[idx])
    #   R5 = DMEM[R3 + 32]      (B[idx])
    #   R6 = R4 * R5
    #   R7 = DMEM[R3 + 64]      (D[idx])
    #   R6 = R6 + R7
    #   DMEM[R3 + 96] = R6      (C[idx])
    #   HALT
    prog = [
        encode("SHL",  rd="R3", rs1="WID", imm16=2),
        encode("ADD",  rd="R3", rs1="R3",  rs2="TID"),
        encode("LD",   rd="R4", rs1="R3",  imm16=0),
        encode("LD",   rd="R5", rs1="R3",  imm16=32),
        encode("MUL",  rd="R6", rs1="R4",  rs2="R5"),
        encode("LD",   rd="R7", rs1="R3",  imm16=64),
        encode("ADD",  rd="R6", rs1="R6",  rs2="R7"),
        encode("ST",   rs1="R3", rs2="R6", imm16=96),
        encode("HALT"),
    ]
    for pc, ins in enumerate(prog):
        await mmio_write(dut, 1, pc, ins)

    # Launch 4 warps (16 SIMT threads total)
    for wid in range(4):
        await launch_warp(dut, wid)

    for i in range(16):
        got = await mmio_read(dut, 2, 96 + i)
        exp = A[i] * B[i] + D[i]
        assert got == exp, f"Thread {i} mismatch: got {got}, exp {exp}"


@cocotb.test()
async def test_virtual_gpu_shader_render(dut):
    """Execute a 32x32 RGB pixel shader on the Verilog SIMT GPU and save nk_gpu_render.png."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    W, H = 32, 32
    fb = [[(0, 0, 0) for _ in range(W)] for _ in range(H)]

    async def px_capture():
        while True:
            await RisingEdge(dut.clk)
            if dut.px_valid.value:
                x = int(dut.px_x.value)
                y = int(dut.px_y.value)
                rgb = int(dut.px_rgb.value)
                if 0 <= x < W and 0 <= y < H:
                    fb[y][x] = ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)

    cocotb.start_soon(px_capture())

    # Shader per row y (8 warps per row: x = (wid << 2) + tid):
    #   R3 = (WID << 2) + TID  (x coord 0..31)
    #   R4 = DMEM[0]           (y coord passed by host)
    #   R5 = (x * 8) << 16     (Red channel gradient)
    #   R6 = (y * 8) << 8      (Green channel gradient)
    #   R7 = ((x ^ y) * 8)     (Blue interference pattern -> approximated with (x+y)*4)
    #   SETPX x=R3, rgb=R5, y=imm16
    for y in range(H):
        # RGB565: R5=x[4:0]<<11, G6=(y*2)[5:0]<<5, B5=((x+y)>>1)[4:0]
        prog = [
            encode("SHL",   rd="R3", rs1="WID", imm16=2),
            encode("ADD",   rd="R3", rs1="R3",  rs2="TID"),
            encode("SHL",   rd="R5", rs1="R3",  imm16=11),
            encode("MOVI",  rd="R6", imm16=(y * 2) << 5),
            encode("OR",    rd="R5", rs1="R5",  rs2="R6"),
            encode("ADDI",  rd="R7", rs1="R3",  imm16=y),
            encode("SHR",   rd="R7", rs1="R7",  imm16=1),
            encode("MOVI",  rd="R4", imm16=31),
            encode("AND",   rd="R7", rs1="R7",  rs2="R4"),
            encode("OR",    rd="R5", rs1="R5",  rs2="R7"),
            encode("SETPX", rs1="R3", rs2="R5", imm16=y),
            encode("HALT"),
        ]
        for pc, ins in enumerate(prog):
            await mmio_write(dut, 1, pc, ins)
        for wid in range(W // 4):
            await launch_warp(dut, wid)

    out_png = os.path.join(os.path.dirname(__file__), "nk_gpu_render.png")
    save_png_rgb(out_png, W, H, fb)
    assert os.path.exists(out_png) and os.path.getsize(out_png) > 100
    assert fb[0][0] == (0, 0, 0)
    assert fb[31][31][0] == 248 and fb[31][31][1] == 248
