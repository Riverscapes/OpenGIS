#!/bin/bash
set -eu
IFS=$'\n\t'

# These environment variables need to be present before the script starts
(: "${HUC?}")
(: "${PROGRAM?}")
(: "${RS_CONFIG?}")
(: "${TAGS?}")

echo "$RS_CONFIG" > /root/.riverscapes

cat<<EOF

   ▄████████  ▄█   ▄█    █▄     ▄████████    ▄████████    ▄████████  ▄████████    ▄████████    ▄███████▄    ▄████████    ▄████████ 
  ███    ███ ███  ███    ███   ███    ███   ███    ███   ███    ███ ███    ███   ███    ███   ███    ███   ███    ███   ███    ███ 
  ███    ███ ███▌ ███    ███   ███    █▀    ███    ███   ███    █▀  ███    █▀    ███    ███   ███    ███   ███    █▀    ███    █▀  
 ▄███▄▄▄▄██▀ ███▌ ███    ███  ▄███▄▄▄      ▄███▄▄▄▄██▀   ███        ███          ███    ███   ███    ███  ▄███▄▄▄       ███        
▀▀███▀▀▀▀▀   ███▌ ███    ███ ▀▀███▀▀▀     ▀▀███▀▀▀▀▀   ▀███████████ ███        ▀███████████ ▀█████████▀  ▀▀███▀▀▀     ▀███████████ 
▀███████████ ███  ███    ███   ███    █▄  ▀███████████          ███ ███    █▄    ███    ███   ███          ███    █▄           ███ 
  ███    ███ ███  ███    ███   ███    ███   ███    ███    ▄█    ███ ███    ███   ███    ███   ███          ███    ███    ▄█    ███ 
  ███    ███ █▀    ▀██████▀    ██████████   ███    ███  ▄████████▀  ████████▀    ███    █▀   ▄████▀        ██████████  ▄████████▀  
  ███    ███                                ███    ███                                                                             
    ███      ▄██████▄   ▄██████▄   ▄█          ▄████████                                                                           
▀█████████▄ ███    ███ ███    ███ ███         ███    ███                                                                           
   ▀███▀▀██ ███    ███ ███    ███ ███         ███    █▀                                                                            
    ███   ▀ ███    ███ ███    ███ ███         ███                                                                                  
    ███     ███    ███ ███    ███ ███       ▀███████████                                                                           
    ███     ███    ███ ███    ███ ███                ███                                                                           
    ███     ███    ███ ███    ███ ███▌    ▄    ▄█    ███                                                                           
   ▄████▀    ▀██████▀   ▀██████▀  █████▄▄██  ▄████████▀                                                                            
                                  ▀                                                                                                
                                     
EOF

echo "HUC: $HUC"
echo "PROGRAM: $PROGRAM"
echo "TAGS: $TAGS"


# Drop into our venv immediately
source /usr/local/venv/bin/activate

echo "======================  GDAL Version ======================="
gdal-config --version

# Define some folders that we can easily clean up later
RS_CONTEXT_DIR=/usr/local/data/$HUC/rs_context
RSC_TASK_SCRATCH=/usr/local/data/$HUC/rs_context_scratch

VBET_DIR=/usr/local/data/$HUC/vbet
BRAT_DIR=/usr/local/data/$HUC/brat
CONFINEMENT_DIR=/usr/local/data/$HUC/confinement
RVD_DIR=/usr/local/data/$HUC/rvd


##########################################################################################
# First Run RS_Context
##########################################################################################

try() {

  rscontext $HUC \
    /shared/NationalDatasets/landfire/200/us_200evt.tif \
    /shared/NationalDatasets/landfire/200/us_200bps.tif \
    /shared/NationalDatasets/ownership/surface_management_agency.shp \
    /shared/NationalDatasets/ownership/FairMarketValue.tif \
    /shared/NationalDatasets/ecoregions/us_eco_l3_state_boundaries.shp \
    /shared/download/prism \
    $RS_CONTEXT_DIR \
    /shared/download/ \
    --meta "Runner=Cybercastor" \
    --parallel \
    --temp_folder $RSC_TASK_SCRATCH \
    --verbose
  if [[ $? != 0 ]]; then return 1; fi
  echo "<<RS_CONTEXT COMPLETE>>"

  # Upload the HUC into the warehouse
  cd $RS_CONTEXT_DIR
  rscli upload . --replace --tags "$TAGS"  --no-input --verbose --program "$PROGRAM"
  if [[ $? != 0 ]]; then return 1; fi
  echo "<<RS_CONTEXT UPLOAD COMPLETE>>"

  ##########################################################################################
  # Now Run VBET
  ##########################################################################################

  vbet $HUC \
    "KW_TESTING" \
    FLOWLINES=$RS_CONTEXT_DIR/hydrology/hydrology.gpkg/network,FLOW_AREA=$RS_CONTEXT_DIR/hydrology/NHDArea.shp,SLOPE_RASTER=$RS_CONTEXT_DIR/topography/slope.tif,DEM=$RS_CONTEXT_DIR/topography/dem.tif,HILLSHADE=$RS_CONTEXT_DIR/topography/dem_hillshade.tif,CATCHMENTS=$RS_CONTEXT_DIR/hydrology/NHDPlusCatchment.shp \
    $VBET_DIR \
    $RS_CONTEXT_DIR/hydrology/nhd_data.sqlite/NHDPlusFlowlineVAA \
    --reach_codes 33400,46003,46006,46007,55800 \
    --create_centerline \
    --meta "Runner=Cybercastor" \
    --verbose
  if [[ $? != 0 ]]; then return 1; fi

  cd /usr/local/src/riverscapes-tools/packages/vbet
  /usr/local/venv/bin/python -m vbet.vbet_rs \
    $VBET_DIR/project.rs.xml \
    $RS_CONTEXT_DIR/project.rs.xml

  echo "<<VBET COMPLETE>>"

  echo "======================  Final Disk space usage ======================="
  df -h
  echo "======================  Upload to the warehouse ======================="



  # Upload the HUC into the warehouse
  cd $VBET_DIR
  rscli upload . --replace --tags "$TAGS" --no-input --verbose --program "$PROGRAM"
  if [[ $? != 0 ]]; then return 1; fi
  echo "<<VBET UPLOAD COMPLETE>>"

  # Cleanup
  echo "<<RSC and VBET PROCESS COMPLETE>>"

}
try || {
  # Emergency Cleanup. We need VBET for everything else so no point in continuing
  echo "<<RS CONTEXT PROCESS ENDED WITH AN ERROR>>"
  EXIT_CODE=1
  exit $EXIT_CODE
}

##########################################################################################
# Now Run BRAT
##########################################################################################
try() {

  ##########################################################################################
  # Now Run BRAT Build
  ##########################################################################################
  bratbuild $HUC \
    $RS_CONTEXT_DIR/topography/dem.tif \
    $RS_CONTEXT_DIR/topography/slope.tif \
    $RS_CONTEXT_DIR/topography/dem_hillshade.tif \
    $RS_CONTEXT_DIR/hydrology/hydrology.gpkg/network_intersected_300m \
    $RS_CONTEXT_DIR/vegetation/existing_veg.tif \
    $RS_CONTEXT_DIR/vegetation/historic_veg.tif \
    $VBET_DIR/outputs/vbet.gpkg/vbet_50 \
    $RS_CONTEXT_DIR/transportation/roads.shp \
    $RS_CONTEXT_DIR/transportation/railways.shp \
    $RS_CONTEXT_DIR/transportation/canals.shp \
    $RS_CONTEXT_DIR/ownership/ownership.shp \
    30 \
    100 \
    100 \
    $BRAT_DIR \
    --reach_codes 33400,33600,33601,33603,46000,46003,46006,46007,55800 \
    --canal_codes 33600,33601,33603 \
    --peren_codes 46006 \
    --flow_areas $RS_CONTEXT_DIR/hydrology/NHDArea.shp \
    --waterbodies $RS_CONTEXT_DIR/hydrology/NHDWaterbody.shp \
    --max_waterbody 0.001 \
    --meta "Runner=Cybercastor" \
    --verbose
  if [[ $? != 0 ]]; then return 1; fi


  # Upload the HUC into the warehouse. This is useful
  # Since BRAT RUn might fail
  cd $BRAT_DIR
  rscli upload . --tags "$TAGS" --replace --no-input --verbose --program "$PROGRAM"
  if [[ $? != 0 ]]; then return 1; fi
  
  ##########################################################################################
  # Now Run BRAT Run
  ##########################################################################################
  bratrun $BRAT_DIR --verbose
  if [[ $? != 0 ]]; then return 1; fi

  cd /usr/local/src/riverscapes-tools/packages/brat
  /usr/local/venv/bin/python -m sqlbrat.brat_rs \
    $BRAT_DIR/project.rs.xml \
    "$RS_CONTEXT_DIR/project.rs.xml,$VBET_DIR/project.rs.xml"

  echo "======================  Final Disk space usage ======================="
  df -h

  echo "======================  Upload to the warehouse ======================="

  # Upload the HUC into the warehouse
  cd $BRAT_DIR
  rscli upload . --tags "$TAGS" --replace --no-input --verbose --program "$PROGRAM"
  if [[ $? != 0 ]]; then return 1; fi

  echo "<<BRAT PROCESS COMPLETE>>\n\n"

}
try || {
  # We continue on to confinement
  echo "<<BRAT PROCESS ENDED WITH AN ERROR>>\n\n"
  EXIT_CODE=1
}


##########################################################################################
# Now Run Confinement
##########################################################################################
try() {

  confinement $HUC \
    $RS_CONTEXT_DIR/hydrology/hydrology.gpkg/network_intersected_300m \
    $VBET_DIR/outputs/vbet.gpkg/vbet_50 \
    $CONFINEMENT_DIR \
    BFwidth \
    ValleyBottom \
    --meta "Runner=Cybercastor" \
    --reach_codes 33400,46003,46006,46007,55800 \
    --verbose

  if [[ $? != 0 ]]; then return 1; fi


  cd /usr/local/src/riverscapes-tools/packages/gnat
  /usr/local/venv/bin/python -m gnat.confinement_rs \
    $CONFINEMENT_DIR/project.rs.xml \
    "$RS_CONTEXT_DIR/project.rs.xml,$VBET_DIR/project.rs.xml"

  echo "======================  Final Disk space usage ======================="
  df -h

  echo "======================  Upload to the warehouse ======================="

  # Upload the HUC into the warehouse.
  cd $CONFINEMENT_DIR
  rscli upload . --tags "$TAGS" --replace --no-input --verbose --program "$PROGRAM"
  if [[ $? != 0 ]]; then return 1; fi

  echo "<<CONFINEMENT PROCESS COMPLETE>>"

}
try || {
  # Fail but move on to RVD
  echo "<<CONFINEMENT PROCESS ENDED WITH AN ERROR>>"
  EXIT_CODE=1
}


##########################################################################################
# Now Run RVD
##########################################################################################
try() {

  rvd $HUC \
      $RS_CONTEXT_DIR/hydrology/hydrology.gpkg/network_intersected_300m \
      $RS_CONTEXT_DIR/vegetation/existing_veg.tif \
      $RS_CONTEXT_DIR/vegetation/historic_veg.tif \
      $VBET_DIR/outputs/vbet.gpkg/vbet_50 \
      $RVD_DIR \
      --reach_codes 33400,46003,46006,46007,55800 \
      --flow_areas $RS_CONTEXT_DIR/hydrology/NHDArea.shp \
      --waterbodies $RS_CONTEXT_DIR/hydrology/NHDWaterbody.shp \
      --meta "Runner=Cybercastor" \
      --verbose
  if [[ $? != 0 ]]; then return 1; fi


  cd /usr/local/src/riverscapes-tools/packages/rvd
  /usr/local/venv/bin/python -m rvd.rvd_rs \
    $RVD_DIR/project.rs.xml \
    "$RS_CONTEXT_DIR/project.rs.xml,$VBET_DIR/project.rs.xml"

  echo "======================  Final Disk space usage ======================="
  df -h

  echo "======================  Upload to the warehouse ======================="

  # Upload the HUC into the warehouse.
  cd $RVD_DIR
  rscli upload . --tags "$TAGS" --replace --no-input --verbose --program "$PROGRAM"
  if [[ $? != 0 ]]; then return 1; fi
  echo "<<RVD PROCESS COMPLETE>>"

}
try || {
  # Emergency Cleanup
  echo "<<RVD PROCESS ENDED WITH AN ERROR>>"
  EXIT_CODE=1
}

exit $EXIT_CODE