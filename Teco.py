from frvsr import *
from torchvision.transforms import functional

VGG_MEAN = [123.68, 116.78, 103.94]
identity = torch.nn.Identity()


class EMA(nn.Module):
    def __init__(self, mu):
        super(EMA, self).__init__()
        self.mu = mu
        self.shadow = {}

    def register(self, name, val):
        self.shadow[name] = val.clone()

    def forward(self, name, x):
        assert name in self.shadow
        new_average = self.mu * x + (1.0 - self.mu) * self.shadow[name]
        self.shadow[name] = new_average.clone()
        return new_average

def VGG19_slim(input, reuse, deep_list=None, norm_flag=True):
    input_img = deprocess(input)
    input_img_ab = input_img * 255.0 - torch.tensor(VGG_MEAN)
    model = VGG19()
    _, output = model(input_img_ab)

    results = {}
    for key in output:
        if deep_list is None or key in deep_list:
            orig_deep_feature = output[key]
            if norm_flag:
                orig_len = torch.sqrt(torch.min(torch.square(orig_deep_feature), dim=1, keepdim=True) + 1e-12)
                results[key] = orig_deep_feature / orig_len
            else:
                results[key] = orig_deep_feature
    return results


def discriminator_F(dis_inputs, FLAGS=None):
    if FLAGS is None:
        raise ValueError("No FLAGS is provided for generator")

    def discriminator_block(inputs, output_channel, kernel_size, stride):
        net = conv2(inputs, kernel_size, stride, use_bias=False)
        net = batchnorm(net, is_training=True)
        net = lrelu(net, 0.2)
        return net

    layer_list = []

    net = conv2(dis_inputs, 3, 64, 1)
    net = lrelu(net, 0.2)

    # block1
    net = discriminator_block(net, 64, 4, 2)
    layer_list += [net]

    # block2
    net = discriminator_block(net, 64, 4, 2)
    layer_list += [net]

    # block3
    net = discriminator_block(net, 128, 4, 2)
    layer_list += [net]

    # block4
    net = discriminator_block(net, 256, 4, 2)
    layer_list += [net]

    net = denselayer(net, 1)
    net = torch.sigmoid(net)

    return net, layer_list


def TecoGAN(r_inputs, r_targets, FLAGS, Global_step, GAN_FLAG=True):
    Global_step += 1
    inputimages = FLAGS.RNN_N
    if FLAGS.pingpang:
        r_inputs_rev_input = r_inputs[:, -2::-1, :, :, :]
        r_targets_rev_input = r_targets[:, -2::-1, :, :, :]
        r_inputs = torch.cat([r_inputs, r_inputs_rev_input], axis=1)
        r_targets = torch.cat([r_targets, r_targets_rev_input], axis=1)
        inputimages = FLAGS.RNN_N * 2 - 1

    output_channel = list(r_targets.shape())[-1]

    gen_outputs, gen_warppre = [], []
    learning_rate = FLAGS.learning_rate
    Frame_t_pre = r_inputs[:, 0:-1, :, :, :]
    Frame_t = r_inputs[:, 1:, :, :, :]

    fnet_input = torch.cat((Frame_t_pre, Frame_t), dim=2)
    fnet_input = torch.reshape(fnet_input, (
        FLAGS.batch_size * (inputimages - 1), 2 * output_channel, FLAGS.crop_size, FLAGS.crop_size))
    gen_flow_lr = fnet(fnet_input)
    gen_flow = upscale_four(gen_flow_lr * 4.)
    gen_flow = torch.reshape(gen_flow,
                             (FLAGS.batch_size, (inputimages - 1), output_channel, FLAGS.crop_size, FLAGS.crop_size))
    input_frames = torch.reshape(Frame_t,
                                 (FLAGS.batch_size * inputimages - 1, output_channel, FLAGS.crop_size, FLAGS.crop_size))
    s_input_warp = F.grid_sample(torch.reshape(Frame_t_pre, (
        FLAGS.batch_size * (inputimages - 1), output_channel, FLAGS.crop_size, FLAGS.crop_size)), gen_flow_lr)

    input0 = torch.cat(
        (r_inputs[:, 0, :, :, :], torch.zeros(size=(FLAGS.batch_size, 3 * 4 * 4, FLAGS.crop_size, FLAGS.crop_size),
                                              dtype=torch.float32)), dim=1)
    gen_pre_output = generator_F(input0, output_channel, FLAGS=FLAGS)

    gen_pre_output = gen_pre_output.view(FLAGS.batch_size, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4)
    gen_outputs.append(gen_pre_output)

    for frame_i in range(inputimages - 1):
        cur_flow = gen_flow[:, frame_i, :, :, :]
        cur_flow = cur_flow.view(FLAGS.batch_size, 2, FLAGS.crop_size * 4, FLAGS.crop_size * 4)
        gen_pre_output_warp = F.grid_sample(gen_pre_output, cur_flow)
        gen_warppre.append(gen_pre_output_warp)
        gen_pre_output_warp = preprocessLr(deprocess(gen_pre_output_warp))

        gen_pre_output_reshape = gen_pre_output_warp.view(FLAGS.batch_size, 3, FLAGS.crop_size, 4, FLAGS.crop_size, 4)
        gen_pre_output_reshape = gen_pre_output_reshape.permute(0, 1, 3, 5, 2, 4)

        gen_pre_output_reshape = torch.reshape(gen_pre_output_reshape,
                                               (FLAGS.batch_size, 3 * 4 * 4, FLAGS.crop_size, FLAGS.crop_size))
        inputs = torch.cat((r_inputs[:, frame_i + 1, :, :, :], gen_pre_output_reshape), dim=1)

        gen_output = generator_F(inputs, output_channel, FLAGS=FLAGS)
        gen_outputs.append(gen_output)
        gen_pre_output = gen_output
        gen_pre_ouput = gen_pre_ouput.view(FLAGS.batch_size, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4)

    gen_outputs = torch.stack(gen_outputs, dim=1)

    gen_outputs = gen_outputs.view(FLAGS.batch_size, inputimages, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4)

    gen_warppre = torch.stack(gen_warppre, dim=1)

    gen_warppre = gen_warppre.view(FLAGS.batch_size, inputimages, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4)

    s_gen_output = torch.reshape(gen_outputs,
                                 (FLAGS.batch_size * inputimages, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
    s_targets = torch.reshape(r_targets, (FLAGS.batch_size * inputimages, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4))

    update_list = []
    update_list_name = []

    if FLAGS.vgg_scaling > 0.0:
        vgg_layer_labels = ['vgg_19/conv2_2', 'vgg_19/conv3_4', 'vgg_19/conv4_4']
        gen_vgg = VGG19_slim(s_gen_output, deep_list=vgg_layer_labels)
        target_vgg = VGG19_slim(s_targets, deep_list=vgg_layer_labels)

    if (GAN_FLAG):
        t_size = int(3 * (inputimages // 3))
        t_gen_output = torch.reshape(gen_outputs[:, :t_size, :, :, :],
                                     (FLAGS.batch_size * t_size, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
        t_targets = torch.reshape(r_targets[:, :t_size, :, :, :],
                                  (FLAGS.batch_size * t_size, 3, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
        t_batch = FLAGS.batch_size * t_size // 3

        if not FLAGS.pingpang:
            fnet_input_back = torch.cat((r_inputs[:, 2:t_size:3, :, :, :], r_inputs[:, 1:t_size:3, :, :, :]), dim=1)
            fnet_input_back = torch.reshape(fnet_input_back,
                                            (t_batch, 2 * output_channel, FLAGS.crop_size, FLAGS.crop_size))

            gen_flow_back_lr = fnet(fnet_input_back)

            gen_flow_back = upscale_four(gen_flow_back_lr * 4.0)

            gen_flow_back = torch.reshape(gen_flow_back,
                                          (FLAGS.batch_size, t_size // 3, 2, FLAGS.crop_size * 4, FLAGS.crop_size * 4))

            T_inputs_VPre_batch = identity(gen_flow[:, 0:t_size:3, :, :, :])
            T_inputs_V_batch = torch.zeros_like(T_inputs_VPre_batch)
            T_inputs_VNxt_batch = T_inputs_V_batch

        else:
            T_inputs_VPre_batch = identity(gen_flow[:, 0:t_size:3, :, :, :])
            T_inputs_V_batch = torch.zeros_like(T_inputs_VPre_batch)
            T_inputs_VNxt_batch = gen_flow[:, -2:-1 - t_size:-3, :, :, :]

        T_vel = torch.stack([T_inputs_VPre_batch, T_inputs_V_batch, T_inputs_VNxt_batch], axis=2)
        T_vel = torch.reshape(T_vel, (FLAGS.batch_size * t_size, 2, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
        T_vel = T_vel.detach()

        if (FLAGS.crop_dt < 1.0):
            crop_size_dt = int(FLAGS.crop_size * 4 * FLAGS.crop_dt)
            offset_dt = (FLAGS.crop_size * 4 - crop_size_dt) // 2
            crop_size_dt = FLAGS.crop_size * 4 - offset_dt * 2
            paddings = torch.tensor([[0, 0], [offset_dt, offset_dt], [offset_dt, offset_dt], [0, 0]])
        real_warp0 = F.grid_sample(t_targets, T_vel)

        real_warp = torch.reshape(real_warp0, (t_batch, 9, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
        if (FLAGS.crop_dt < 1.0):
            real_warp = functional.resized_crop(real_warp, offset_dt, offset_dt, crop_size_dt, crop_size_dt)

        if (FLAGS.Dt_mergeDs):
            if (FLAGS.crop_dt < 1.0):
                real_warp = F.pad(real_warp, paddings, "constant")
            before_warp = torch.reshape(t_targets, (t_batch, 9, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
            t_input = torch.reshape(r_inputs[:, :t_size, :, :, :],
                                    (t_batch, 9, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
            input_hi = functional.resize(t_input, [FLAGS.crop_size * 4, FLAGS.crop_size * 4])
            real_warp = torch.cat((before_warp, real_warp, input_hi), dim=1)

            tdiscrim_real_output, real_layers = discriminator_F(real_warp, FLAGS=FLAGS)

        else:
            tdiscrim_real_output = discriminator_F(real_warp, FLAGS=FLAGS)

        fake_warp0 = F.grid_sample(t_gen_output, T_vel)

        fake_warp = torch.reshape(fake_warp0, (t_batch, 9, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
        if (FLAGS.crop_dt < 1.0):
            fake_warp = functional.resized_crop(fake_warp, offset_dt, offset_dt, crop_size_dt, crop_size_dt)

        if (FLAGS.Dt_mergeDs):
            if (FLAGS.crop_dt < 1.0):
                fake_warp = F.pad(fake_warp, paddings, "constant")
            before_warp = torch.reshape(before_warp, (t_batch, 9, FLAGS.crop_size * 4, FLAGS.crop_size * 4))
            fake_warp = torch.cat((before_warp, fake_warp, input_hi), dim=1)
            tdiscrim_fake_output, fake_layers = discriminator_F(fake_warp, FLAGS=FLAGS)

        else:
            tdiscrim_fake_output = discriminator_F(fake_warp, FLAGS=FLAGS)

        if (FLAGS.D_LAYERLOSS):
            Fix_Range = 0.02
            Fix_margin = 0.0

            sum_layer_loss = 0
            d_layer_loss = 0

            layer_loss_list = []
            layer_n = len(real_layers)

            layer_norm = [12.0, 14.0, 24.0, 100.0]
            for layer_i in range(layer_n):
                real_layer = real_layers[layer_i]
                false_layer = fake_layers[layer_i]

                layer_diff = real_layer - false_layer
                layer_loss = torch.mean(torch.sum(torch.abs(layer_diff), dim=[3]))

                layer_loss_list += [layer_loss]

                scaled_layer_loss = Fix_Range * layer_loss / layer_norm[layer_i]

                sum_layer_loss += scaled_layer_loss
                if Fix_margin > 0.0:
                    d_layer_loss += torch.max(0.0, torch.tensor(Fix_margin - scaled_layer_loss))

            update_list += layer_loss_list
            update_list_name += [("D_layer_%d_loss" % _) for _ in range(layer_n)]
            update_list += [sum_layer_loss]
            update_list_name += ["D_layer_loss_sum"]

            if Fix_margin > 0.0:
                update_list += [d_layer_loss]
                update_list_name += ["D_layer_loss_for_D_sum"]
    diff1_mse = s_gen_output - s_targets

    content_loss = torch.mean(torch.sum(torch.square(diff1_mse), dim=[3]))
    update_list += [content_loss]
    update_list_name += ["l2_content_loss"]
    gen_loss = content_loss

    diff2_mse = input_frames - s_input_warp

    warp_loss = torch.mean(torch.sum(torch.square(diff2_mse), dim=[3]))
    update_list += [warp_loss]
    update_list_name += ["l2_warp_loss"]

    vgg_loss = None
    vgg_loss_list = []
    if FLAGS.vgg_scaling > 0.0:
        vgg_wei_list = [1.0, 1.0, 1.0, 1.0]
        vgg_loss = 0
        vgg_layer_n = len(vgg_layer_labels)

        for layer_i in range(vgg_layer_n):
            curvgg_diff = torch.sum(gen_vgg[vgg_layer_labels[layer_i]] * target_vgg[vgg_layer_labels[layer_i]], dim=[3])
            scaled_layer_loss = vgg_wei_list[layer_i] * curvgg_diff
            vgg_loss_list += [curvgg_diff]
            vgg_loss += scaled_layer_loss

        gen_loss += FLAGS.vgg_scaling * vgg_loss
        vgg_loss_list += [vgg_loss]

        update_list += vgg_loss_list
        update_list_name += ["vgg_loss_%d" % (_ + 2) for _ in range(len(vgg_loss_list) - 1)]
        update_list_name += ["vgg_all"]

    if FLAGS.pingpang:
        gen_out_first = gen_outputs[:, 0:FLAGS.RNN_N - 1, :, :, :]
        gen_out_last_rev = gen_outputs[:, -1:-FLAGS.RNN_N:-1, :, :, :]

        pploss = torch.mean(torch.abs(gen_out_first - gen_out_last_rev))

        if FLAGS.pp_scaling > 0:
            gen_loss += pploss * FLAGS.pp_scaling
        update_list += [pploss]
        update_list_name += ["PingPang"]

    if(GAN_FLAG):
        t_adversarial_loss = torch.mean(-torch.log(tdiscrim_fake_output + FLAGS.EPS))
        dt_ratio = torch.min(FLAGS.Dt_ratio_max, FLAGS.Dt_ratio_0 + FLAGS.Dt_ratio_add * torch.tensor(Global_step,dtype=torch.float32))

    gen_loss += FLAGS.ratio * t_adversarial_loss
    update_list += [t_adversarial_loss]
    update_list_name += ["t_adversarial_loss"]

    if(FLAGS.D_LAYERLOSS):
        gen_loss += sum_layer_loss * dt_ratio

    if(GAN_FLAG):
        t_discrim_fake_loss = torch.log(1-tdiscrim_fake_output+FLAGS.EPS)
        t_discrim_real_loss = torch.log(tdiscrim_real_output+FLAGS.EPS)

        t_discrim_loss = torch.mean(-(t_discrim_fake_loss + t_discrim_real_loss))
        t_balance = torch.mean(t_discrim_real_loss) + t_adversarial_loss

        update_list += [t_discrim_loss]
        update_list_name += ["t_discrim_loss"]

        update_list += [torch.mean(tdiscrim_real_output), torch.mean(tdiscrim_fake_output)]
        update_list_name += ["t_discrim_real_output", "t_discrim_fake_output"]

        if(FLAGS.D_LAYERLOSS and Fix_margin>0.0):
            discrim_loss = t_discrim_loss + d_layer_loss * dt_ratio

        else:
            discrim_loss = t_discrim_loss

        tb_exp_averager = EMA(0.99)
        init_average = torch.zeros_like(t_balance)
        tb_exp_averager.register("TB_average", init_average)
        tb = tb_exp_averager.forward("TB_average", t_balance)

