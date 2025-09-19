2.0: MESH Hydrology parameters input file (Version 2.0)
##### Option Flags #####
----#
    0                                                       # Number of option flags
##### Channel routing parameters per river class #####
-------#
4                                                           # Number of channel routing parameters
R2N            _R2N     _R2N     _R2N     _R2N     _R2N     # only used with new routing
R1N            _R1N     _R1N     _R1N     _R1N     _R1N     # only used with new routing
PWR            _PWR     _PWR     _PWR     _PWR     _PWR     # only used with BASEFLOWFLAG wf_lzs
FLZ            _FLZ     _FLZ     _FLZ     _FLZ     _FLZ     # only used with BASEFLOWFLAG wf_lzs
##### GRU class independent hydrologic parameters #####     # 10comment line 13                                                           | *
-------#                                                    # 11comment line 14                                                           | *
       0                                                    # Number of GRU independent hydrologic parameters
##### GRU class dependent hydrologic parameters #####       # 18comment line 16                                                           | *
-------#                                                    # 19comment line 17                                                           | *
       4                                                    # 21Number of GRU dependent hydrologic parameters     4 here                  | I8
!      NLForest(1)  BLForest(5) MForest(6)  Shrublandsub(8) Grasslandsub(10)  SLichenmoss(11)     GLichenmoss(12)  Cropland(15)  Barrenland(16)  Urban(17)  Water(18)  SnowIce(19)
ZSNL   _1ZSNL       0.140       _6ZSNL      _8ZSNL          _10ZSNL           0.057               0.057            0.100         _16ZSNL         0.350      0.100      0.05
ZPLS   _1ZPLS       0.040       _6ZPLS      _8ZPLS          _10ZPLS           0.021               0.021            0.090         _16ZPLS         0.090      0.100      0.039
ZPLG   _1ZPLG       0.140       _6ZPLG      _8ZPLG          _10ZPLG           0.020               0.020            0.300         _16ZPLG         0.260      0.100      0.092
IWF    1            1           1           1               1                 1                   1                1             1               1          0          1
