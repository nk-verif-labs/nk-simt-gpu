SIM ?= icarus
TOPLEVEL_LANG ?= verilog
VERILATOR_COVERAGE ?= 0

VERILOG_SOURCES += $(CURDIR)/../rtl/nk_gpu_lane.v
VERILOG_SOURCES += $(CURDIR)/../rtl/nk_gpu_top.v

TOPLEVEL = nk_gpu_top
MODULE ?= test_nk_gpu,test_nk_gpu_verif

export PYTHONPATH := $(CURDIR):$(CURDIR)/../driver:$(PYTHONPATH)

ifeq ($(SIM),icarus)
COMPILE_ARGS += -I$(CURDIR)/../rtl
endif

ifeq ($(SIM),verilator)
EXTRA_ARGS += -I$(CURDIR)/../rtl --Wall -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL
ifeq ($(VERILATOR_COVERAGE),1)
COMPILE_ARGS += --coverage
endif
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
