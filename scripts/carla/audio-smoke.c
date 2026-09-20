#include <math.h>
#include <stdio.h>
#include <string.h>

#include <ogg/ogg.h>
#include <opus.h>

static int check_ogg(void)
{
    unsigned char payload[] = "CARLA ARM64 Ogg packet round trip";
    ogg_stream_state writer, reader;
    ogg_sync_state sync;
    ogg_packet input = {0}, output = {0};
    ogg_page page;
    int result = 1;

    if (ogg_stream_init(&writer, 12345) != 0) return 1;
    if (ogg_stream_init(&reader, 12345) != 0) {
        ogg_stream_clear(&writer);
        return 1;
    }
    if (ogg_sync_init(&sync) != 0) {
        ogg_stream_clear(&reader);
        ogg_stream_clear(&writer);
        return 1;
    }
    input.packet = payload;
    input.bytes = sizeof(payload);
    input.b_o_s = 1;
    input.e_o_s = 1;
    if (ogg_stream_packetin(&writer, &input) != 0) goto done;
    if (ogg_stream_flush(&writer, &page) != 1) goto done;
    long length = page.header_len + page.body_len;
    char *buffer = ogg_sync_buffer(&sync, length);
    if (buffer == NULL) goto done;
    memcpy(buffer, page.header, page.header_len);
    memcpy(buffer + page.header_len, page.body, page.body_len);
    if (ogg_sync_wrote(&sync, length) != 0) goto done;
    if (ogg_sync_pageout(&sync, &page) != 1) goto done;
    if (ogg_stream_pagein(&reader, &page) != 0) goto done;
    if (ogg_stream_packetout(&reader, &output) != 1) goto done;
    if (output.bytes != (long)sizeof(payload)) goto done;
    if (memcmp(output.packet, payload, sizeof(payload)) != 0) goto done;
    result = 0;
done:
    ogg_sync_clear(&sync);
    ogg_stream_clear(&reader);
    ogg_stream_clear(&writer);
    if (result == 0) puts("PASS Ogg packet/page round trip");
    return result;
}

static int check_opus(void)
{
    enum { FRAME_SIZE = 960, FRAMES = 20, RATE = 48000 };
    opus_int16 pcm[FRAME_SIZE], decoded[FRAME_SIZE];
    unsigned char packet[4096];
    int error = OPUS_OK, result = 1, encoded_bytes = 0;
    double energy = 0;
    OpusEncoder *encoder = opus_encoder_create(RATE, 1, OPUS_APPLICATION_AUDIO, &error);
    if (encoder == NULL || error != OPUS_OK) return 1;
    OpusDecoder *decoder = opus_decoder_create(RATE, 1, &error);
    if (decoder == NULL || error != OPUS_OK) {
        opus_encoder_destroy(encoder);
        return 1;
    }
    if (opus_encoder_ctl(encoder, OPUS_SET_BITRATE(64000)) != OPUS_OK) goto done;
    for (int frame = 0; frame < FRAMES; ++frame) {
        for (int sample = 0; sample < FRAME_SIZE; ++sample) {
            double phase = 6.283185307179586 * 440.0 * (frame * FRAME_SIZE + sample) / RATE;
            pcm[sample] = (opus_int16)(10000.0 * sin(phase));
        }
        int bytes = opus_encode(encoder, pcm, FRAME_SIZE, packet, sizeof(packet));
        if (bytes <= 0 || bytes > (int)sizeof(packet)) goto done;
        encoded_bytes += bytes;
        memset(decoded, 0, sizeof(decoded));
        int samples = opus_decode(decoder, packet, bytes, decoded, FRAME_SIZE, 0);
        if (samples != FRAME_SIZE) goto done;
        if (frame >= 3) {
            for (int sample = 0; sample < FRAME_SIZE; ++sample) {
                energy += (double)decoded[sample] * decoded[sample];
            }
        }
    }
    energy /= (FRAMES - 3) * FRAME_SIZE;
    if (!isfinite(energy) || energy < 1000000 || energy > 200000000) goto done;
    printf("PASS Opus encode/decode version=%s bytes=%d samples=%d rms=%.1f\n",
           opus_get_version_string(), encoded_bytes, FRAMES * FRAME_SIZE, sqrt(energy));
    result = 0;
done:
    opus_decoder_destroy(decoder);
    opus_encoder_destroy(encoder);
    return result;
}

int main(void)
{
    if (check_ogg() != 0 || check_opus() != 0) {
        fputs("FAIL audio codec smoke\n", stderr);
        return 1;
    }
    return 0;
}
