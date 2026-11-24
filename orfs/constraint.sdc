create_clock -name clk -period 1500 [get_ports clk]
set_input_delay  250 -clock clk [all_inputs]
set_output_delay 250 -clock clk [all_outputs]
