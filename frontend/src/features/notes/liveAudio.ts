const TARGET_RATE = 16000;

function downsample(samples: Float32Array, sourceRate: number) {
  if (sourceRate === TARGET_RATE) return samples;
  const ratio = sourceRate / TARGET_RATE;
  const result = new Float32Array(Math.floor(samples.length / ratio));
  for (let index = 0; index < result.length; index++) {
    const from = Math.floor(index * ratio);
    const to = Math.min(samples.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let cursor = from; cursor < to; cursor++) sum += samples[cursor];
    result[index] = sum / Math.max(1, to - from);
  }
  return result;
}

export function encodeLiveWav(frames: Float32Array[], sourceRate: number) {
  const length = frames.reduce((total, frame) => total + frame.length, 0);
  const joined = new Float32Array(length);
  let position = 0;
  for (const frame of frames) {
    joined.set(frame, position);
    position += frame.length;
  }
  const samples = downsample(joined, sourceRate);
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index++)
      view.setUint8(offset + index, value.charCodeAt(index));
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_RATE, true);
  view.setUint32(28, TARGET_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => {
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(
      44 + index * 2,
      clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff,
      true,
    );
  });
  return {
    blob: new Blob([buffer], { type: "audio/wav" }),
    duration: samples.length / TARGET_RATE,
  };
}
