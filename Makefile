all: galactocentric.py _astropy_init.py

galactocentric.nc: galactocentric.py
	python galactocentric.py

astropy.nc: _astropy_init.py
	python _astropy_init.py

potential.nc: core.py
	python core.py


potential.integrate_orbit.png: plot_radiation.py ds_density.nc ds_freq.nc ds_gravity.nc ds_waterdepth.nc
	python plot_radiation.py

spherical.png: plot_wave.py ds_wave_cpl.nc
	python plot_wave.py

pericenter_apocenter_eccentricity.png: plot_wave.py ds_wave_cpl.nc
	python plot_wave.py

integrate_orbit.png: plot_wave.py mat_k.nc mat_s.nc
	python plot_wave.py
	
cylindrical.png: plot_wave.py mat_k.nc mat_s.nc
	python plot_wave.py


clean:
	rm *.nc

.PHONY : clean
