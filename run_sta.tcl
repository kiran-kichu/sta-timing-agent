read_liberty /home/kiran/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
read_verilog /home/kiran/OpenROAD-flow-scripts/flow/results/sky130hd/picorv32/p3.6/1_2_yosys.v
link_design picorv32
read_sdc /home/kiran/OpenROAD-flow-scripts/flow/results/sky130hd/picorv32/p3.6/1_synth.sdc

puts "===== WNS ====="
report_wns
puts "===== TNS ====="
report_tns
puts "===== CHECKS ====="
report_checks -path_delay max -sort_by_slack -group_count 8 \
              -format full_clock_expanded \
              -fields {slew cap input_pins fanout}
