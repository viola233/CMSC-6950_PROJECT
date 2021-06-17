all: galactocentric.py _astropy_init.py core.py orbit.py

galactocentric.nc: galactocentric.py
	python galactocentric.py

astropy.nc: _astropy_init.py
	python _astropy_init.py

potential.nc: core.py
	python core.py

orbit.nc:orbit.py
      python orbit.py

potential.integrate_orbit.png: galactocentric.py 
                               _astropy_init.py 
			       core.py
	                       orbit.py

spherical.png: galactocentric.py    
              _astropy_init.py 
              core.py

	

pericenter_apocenter_eccentricity.png:  galactocentric.py
                                        _astropy_init.py 
					core.py orbit.py
	

integrate_orbit.png:  galactocentric.py
                      _astropy_init.py 
		      core.py orbit.py
	
	
cylindrical.png:  galactocentric.py
                  _astropy_init.py
		  core.py orbit.py
	


clean:
	rm *.nc

.PHONY : clean
