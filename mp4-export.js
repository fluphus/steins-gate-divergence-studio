/* Frame-by-frame local MP4 export. Mediabunny is vendored; no CDN or server required. */
(() => {
  'use strict';

  async function exportMp4({ masterUrl, canvas, frameRate, totalFrames, renderFrame, onProgress, signal }) {
    const media = window.Mediabunny;
    if (!media) throw new Error('MP4 组件未加载，请刷新页面后重试。');
    if (typeof VideoEncoder === 'undefined' || typeof VideoDecoder === 'undefined') {
      throw new Error('此浏览器不支持逐帧 MP4 导出，请使用支持 WebCodecs 的新版 Chrome 或 Edge，或使用下方 Python 命令。');
    }
    const quality = new media.Quality({ bitrate: 12_000_000 });
    if (!await media.canEncodeVideo('avc', { width: canvas.width, height: canvas.height, frameRate, quality })) {
      throw new Error('此浏览器无法编码 H.264，请换用新版 Chrome 或 Edge，或使用下方 Python 命令。');
    }

    let input;
    let output;
    let stopped = false;
    let tasks = [];
    const checkCanceled = () => {
      if (stopped || signal.aborted) throw new DOMException('已取消导出', 'AbortError');
    };
    try {
      checkCanceled();
      const response = await fetch(masterUrl, { signal });
      if (!response.ok) throw new Error('无法读取母版视频，请刷新后重试。');
      input = new media.Input({ source: new media.BlobSource(await response.blob()), formats: [media.MP4] });
      const videoTrack = await input.getPrimaryVideoTrack();
      const audioTrack = await input.getPrimaryAudioTrack();
      if (!videoTrack || !await videoTrack.canDecode()) throw new Error('此浏览器无法解码母版视频。');
      if (!audioTrack || audioTrack.codec !== 'aac') throw new Error('母版 AAC 音轨缺失，无法导出带原声的 MP4。');
      const audioConfig = await audioTrack.getDecoderConfig();
      if (!audioConfig) throw new Error('无法读取母版音轨参数。');
      checkCanceled();

      const target = new media.BufferTarget();
      output = new media.Output({ target, format: new media.Mp4OutputFormat({ fastStart: 'in-memory' }) });
      const videoSource = new media.CanvasSource(canvas, { codec: 'avc', quality, keyFrameInterval: 2 });
      const audioSource = new media.EncodedAudioPacketSource('aac');
      output.addVideoTrack(videoSource, { frameRate });
      output.addAudioTrack(audioSource);
      await output.start();

      async function writeVideo() {
        let frame = 0;
        const sink = new media.VideoSampleSink(videoTrack);
        for await (const sample of sink.samples()) {
          try {
            checkCanceled();
            if (frame >= totalFrames) throw new Error('母版视频帧数超出预期。');
            renderFrame(frame, context => sample.draw(context, 0, 0, canvas.width, canvas.height));
            await videoSource.add(frame / frameRate, 1 / frameRate);
            onProgress(++frame, totalFrames);
          } finally {
            sample.close();
          }
          // Let paint, progress and the Cancel button run even on a software encoder.
          if (frame % 2 === 0) await new Promise(resolve => setTimeout(resolve, 0));
        }
        if (frame !== totalFrames) throw new Error(`母版视频只读取到 ${frame} 帧，应为 ${totalFrames} 帧。`);
        videoSource.close();
      }

      async function writeAudio() {
        let first = true;
        for await (const packet of new media.EncodedPacketSink(audioTrack).packets()) {
          checkCanceled();
          await audioSource.add(packet, first ? { decoderConfig: audioConfig } : undefined);
          first = false;
        }
        if (first) throw new Error('母版音轨没有可用数据。');
        audioSource.close();
      }

      tasks = [writeVideo(), writeAudio()];
      await Promise.all(tasks);
      checkCanceled();
      await output.finalize();
      checkCanceled();
      return new Blob([target.buffer], { type: 'video/mp4' });
    } catch (error) {
      stopped = true;
      if (output && output.state !== 'finalized') await output.cancel().catch(() => {});
      await Promise.allSettled(tasks);
      throw error;
    } finally {
      input?.dispose();
    }
  }

  window.DivergenceMp4Exporter = { exportMp4 };
})();
