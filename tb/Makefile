SIM ?= icarus
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES += $(CURDIR)/../rtl/nk_gpu_lane.v
VERILOG_SOURCES += $(CURDIR)/../rtl/nk_gpu_top.v

TOPLEVEL = nk_gpu_top
MODULE = test_nk_gpu

ifeq ($(SIM),icarus)
COMPILE_ARGS += -I$(CURDIR)/../rtl
endif

ifeq ($(SIM),verilator)
EXTRA_ARGS += -I$(CURDIR)/../rtl --Wall -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
