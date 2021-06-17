all: ds_density.nc ds_freq.nc ds_gravity.nc ds_waterdepth.nc 1_radiation.png ds_wave_cpl.nc mat_k.nc mat_s.nc 2_1_correlation.png 2_2_waveshape.png 2_3_infmatrix.png clean 

ds_density.nc: radiation.py
	python radiation.py

ds_freq.nc: radiation.py
	python radiation.py

ds_gravity.nc: radiation.py
	python radiation.py

ds_waterdepth.nc: radiation.py
	python radiation.py

1_radiation.png: plot_radiation.py ds_density.nc ds_freq.nc ds_gravity.nc ds_waterdepth.nc
	python plot_radiation.py

ds_wave_cpl.nc: wave.py
	python wave.py

mat_k.nc: wave.py
	python wave.py

mat_s.nc: wave.py
	python wave.py

2_1_correlation.png: plot_wave.py ds_wave_cpl.nc
	python plot_wave.py

2_2_waveshape.png: plot_wave.py ds_wave_cpl.nc
	python plot_wave.py

2_3_infmatrix.png: plot_wave.py mat_k.nc mat_s.nc
	python plot_wave.py


clean:
	rm *.nc

.PHONY : clean
