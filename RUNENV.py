##
##
## Specify detector and run dependent constants here
##
##

## database files##
##########################################
env_db = 'env.db' #asic config enviornment
run_db = 'run.db' #data log
##########################################

######################################################
## Parameters for automatic file transfer to data drive 
## SHOULD END with /'
destination_dir_='/path/to/data/storage/directory/'
current_dir_='/path/to/current/directory/'
##3###################################################

#####################################################
## Mappings of io_group-->pacman version and io_group/tile-->ASIC version
io_group_pacman_tile_={1:[1]}#, 2:[2]}
iog_pacman_version_={1: 'v1rev3'}#, 2:'v1rev4'}
io_group_asic_version_={1:'lightpix-1'}
#####################################################

#####################################################
## Chips to exclude by io_group, tile from new networks being created.
## For example, {1: {}, 2:{ 1 : [11, 12], 4: [55] }} excludes on io_group 2 chips 11,12 on tile 1, and chip 55 on tile 4 
iog_exclude={1:{} }
####################################################

###################################################
asic_config_dir='asic_configs'
##################################################

###################################################
iog_VDDD_DAC = {1 : 42000}
iog_VDDA_DAC = {1 : 46500}
###################################################

