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
destination_dir_='/data/commission/Feb2024/'
##3###################################################

#####################################################
## Mappings of io_group-->pacman version and io_group/tile-->ASIC version
io_group_pacman_tile_={1:list(range(1,9)), 2:list(range(1,9)), 5:list(range(1,9)), 6: list(range(1,9))}
iog_pacman_version_={1: 'v1rev3b', 2:'v1rev3b', 5:'v1rev4', 6:'v1rev4'}
io_group_asic_version_={1:2, 2:2, 4:'2b', 4:'2b'}
#####################################################

#####################################################
## Chips to exclude by io_group, tile from new networks being created.
## For example, {1: {}, 2:{ 1 : [11, 12], 4: [55] }} excludes on io_group 2 chips 11,12 on tile 1, and chip 55 on tile 4 
iog_exclude={2: {1:[],2:[], 3:[], 4:[], 5:[], 7 : []} }
####################################################

###################################################
asic_config_dir='asic_configs/'
##################################################

###################################################
iog_VDDD_DAC = {1 : 40000, 2: 40000, 5:28500, 6:28500}
iog_VDDA_DAC = {1 : 46500, 2: 46500, 5:46500, 6:46500}
###################################################


