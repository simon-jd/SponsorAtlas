'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import DeckGL from '@deck.gl/react';
import { H3HexagonLayer } from '@deck.gl/geo-layers';
import { motion, useReducedMotion } from 'framer-motion';
import type { MapViewState } from '@deck.gl/core';
import type { Cell } from '../../lib/viz-data';
import { Fallback, CellTable } from './Fallback';
const initial = {
  longitude: -98,
  latitude: 38,
  zoom: 3,
  pitch: 0,
  bearing: 0
};
export default function WorksiteHexMap({
  cells,
  locale,
  resolution,
  onResolutionChange
}: {
  cells: Cell[];
  locale: string;
  resolution: number;
  onResolutionChange: (resolution: number) => void;
}) {
  const previousZoom = useRef(3);
  const zh = locale === 'zh';
  const reduced = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const drawn = useRef(0);
  const layerBuilds=useRef(0);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [view, setView] = useState<MapViewState>(initial);
  useEffect(() => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2');
    setAvailable(Boolean(gl));
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
  }, []);
  useEffect(() => {
    // Programmatic pan/zoom: apply synchronously so performance.measure('viz-script')
    // spans the React + deck.gl prop update of this one input.
    const move = (event: Event) => {
      performance.clearMarks('viz-input');
      performance.mark('viz-input');
      flushSync(() => setView(v => ({
        ...v,
        ...(event as CustomEvent).detail
      })));
      performance.clearMeasures('viz-script'); // keep one entry; no unbounded buffer
      performance.measure('viz-script', 'viz-input');
    };
    window.addEventListener('viz-panzoom', move);
    return () => window.removeEventListener('viz-panzoom', move);
  }, []);
  useEffect(() => {
    const level = Math.max(0, Math.min(7, Math.floor(view.zoom)));
    if (level !== previousZoom.current) {
      previousZoom.current = level;
      onResolutionChange(level);
    }
  }, [view.zoom, onResolutionChange]);
  const layer = useMemo(()=>{layerBuilds.current++;return new H3HexagonLayer<Cell>({
    id: 'aggregated-public-cells',
    data: cells,
    getHexagon: d => d.h3,
    getFillColor: d => [25, 120 + Math.min(90, Math.log2(d.count + 1) * 9), 180, 210],
    getLineColor: [235, 245, 250, 220],
    lineWidthMinPixels: 1,
    stroked: true,
    filled: true,
    extruded: false,
    pickable: false,
    highPrecision: 'auto'
  });},[cells]);
  if (available === false) return <Fallback cells={cells} locale={locale} />;
  return <motion.div ref={ref} data-testid="viz-scene" data-layer-builds={layerBuilds.current} data-cells={cells.length} data-resolution={resolution} data-motion={reduced ? 'none' : 'opacity'} initial={reduced ? false : {
    opacity: 0
  }} animate={{
    opacity: 1
  }} transition={{
    duration: reduced ? 0 : .25
  }}>
 <p>{zh ? '方向键平移；加减按钮缩放。颜色深浅表示申请数量，位置是工作地邮编区或城市的人口普查参考点，不是具体地址。' : 'Arrow keys pan; zoom buttons change scale. Color indicates application count; locations are Census reference points of the worksite ZIP area or city, not street addresses.'}</p>
 <div className="viz-controls">
<button onClick={() => setView(v => ({
        ...v,
        zoom: Math.min(8, v.zoom + 1)
      }))}>{zh ? '放大' : 'Zoom in'}</button>
<button onClick={() => setView(v => ({
        ...v,
        zoom: Math.max(0, v.zoom - 1)
      }))}>{zh ? '缩小' : 'Zoom out'}</button>
<button onClick={() => setView(initial)}>{zh ? '重置视图' : 'Reset view'}</button>
</div>
 <div className="hex-map" role="region" aria-label={zh ? 'H3 地图，方向键平移' : 'H3 map, arrow keys pan'} tabIndex={0} onKeyDown={e => {
      const delta: Record<string, [number, number]> = {
        ArrowLeft: [-3, 0],
        ArrowRight: [3, 0],
        ArrowUp: [0, 3],
        ArrowDown: [0, -3]
      };
      if (delta[e.key]) {
        e.preventDefault();
        const [x, y] = delta[e.key];
        setView(v => ({
          ...v,
          longitude: v.longitude + x,
          latitude: Math.max(-80, Math.min(80, v.latitude + y))
        }));
      }
    }}>
 {available && <DeckGL viewState={view} onViewStateChange={({
        viewState
      }) => setView(viewState as MapViewState)} controller={{
        dragRotate: false
      }} layers={[layer]} useDevicePixels={1} onError={() => setAvailable(false)} onDeviceInitialized={device => {
        const gl = (device as unknown as {
          gl: WebGL2RenderingContext;
        }).gl;
        if (!gl || !ref.current) return;
        const debug = gl.getExtension('WEBGL_debug_renderer_info');
        ref.current.dataset.renderer = String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
        ref.current.dataset.vendor = String(gl.getParameter(debug?.UNMASKED_VENDOR_WEBGL ?? gl.VENDOR));
        ref.current.dataset.context = 'webgl2';
        gl.canvas.addEventListener('webglcontextlost', () => setAvailable(false), {
          once: true
        });
      }} onBeforeRender={() => {
        performance.clearMarks('viz-render-start');
        performance.mark('viz-render-start');
      }} onAfterRender={() => {
        performance.clearMeasures('viz-render');
        performance.measure('viz-render', 'viz-render-start');
        if (ref.current) ref.current.dataset.drawn = String(++drawn.current);
      }} />}
 </div>
<details>
<summary>{zh ? '查看全部单元数据' : 'View all cell data'}</summary>
<CellTable cells={cells} locale={locale} />
</details>
</motion.div>;
}
