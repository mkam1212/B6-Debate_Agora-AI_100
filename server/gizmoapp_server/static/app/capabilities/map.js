export function lonLatToTile({ latitude, longitude, zoom }) {
  const latRad = latitude * Math.PI / 180;
  const scale = 2 ** zoom;
  return {
    x: Math.floor((longitude + 180) / 360 * scale),
    y: Math.floor((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * scale),
    z: zoom,
  };
}


export function openStreetMapTileUrl(template, tile) {
  return template
    .replace("{z}", tile.z)
    .replace("{x}", tile.x)
    .replace("{y}", tile.y);
}
