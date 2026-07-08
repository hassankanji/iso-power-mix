from src.connectors import caiso, ercot, spp, nyiso, pjm, miso, isone

CONNECTORS = {
    caiso.ISO: caiso,
    ercot.ISO: ercot,
    spp.ISO: spp,
    nyiso.ISO: nyiso,
    pjm.ISO: pjm,
    miso.ISO: miso,
    isone.ISO: isone,
}
