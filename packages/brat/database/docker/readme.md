This docker folder contains the definition of the BRAT parameters postgres database
that is running on the Amazon RDS NARDEV server. This is the central place where
NAR and USU enter BRAT parameters. From here the parameters are then downloaded
into this git repository using the `scripts/update_brat_parameters.py` script.

Note that you can use docker to initialize a local copy of this database, but the
SQL files within the `initdb` folder will probably be out of date and lag the 
latest values in this git repository and the live postgres database.