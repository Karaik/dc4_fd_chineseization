using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

// 仅处理显式请求的文件；TLG 像素读写统一使用项目固定的 TlgLib。
internal static class NativeImages
{
    private static MethodInfo Decode, Encode5, Encode6;

    private static string Hash(byte[] bytes)
    {
        using (var hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
    }

    private static byte[] Rgba(Bitmap image)
    {
        int stride = checked(image.Width * 4);
        byte[] rgba = new byte[checked(stride * image.Height)];
        byte[] row = new byte[stride];
        var data = image.LockBits(new Rectangle(0, 0, image.Width, image.Height),
            ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            for (int y = 0; y < image.Height; y++)
            {
                Marshal.Copy(IntPtr.Add(data.Scan0, checked(y * data.Stride)), row, 0, stride);
                for (int x = 0; x < stride; x += 4)
                {
                    int at = y * stride + x;
                    rgba[at] = row[x + 2]; rgba[at + 1] = row[x + 1];
                    rgba[at + 2] = row[x]; rgba[at + 3] = row[x + 3];
                }
            }
        }
        finally { image.UnlockBits(data); }
        return rgba;
    }

    private static Bitmap Tlg(byte[] data)
    {
        return (Bitmap)Decode.Invoke(null, new object[] { data, 0, false });
    }

    private static byte[] RawTlg(byte[] data, out bool wrapped, out byte[] trailer)
    {
        wrapped = data.Length >= 15 && Encoding.ASCII.GetString(data, 0, 11) == "TLG0.0\0sds\x1a";
        trailer = new byte[0];
        if (!wrapped) return data;
        int length = BitConverter.ToInt32(data, 11);
        if (length < 11 || length > data.Length - 15) throw new InvalidDataException("Invalid TLG0 payload length");
        trailer = data.Skip(15 + length).ToArray();
        return data.Skip(15).Take(length).ToArray();
    }

    private static byte[] PreserveShell(byte[] encoded, bool wrapped, byte[] trailer)
    {
        bool ignored;
        byte[] ignoredTrailer;
        byte[] raw = RawTlg(encoded, out ignored, out ignoredTrailer);
        if (!wrapped) return raw;
        using (var output = new MemoryStream())
        using (var writer = new BinaryWriter(output))
        {
            writer.Write(Encoding.ASCII.GetBytes("TLG0.0\0sds\x1a"));
            writer.Write(raw.Length); writer.Write(raw); writer.Write(trailer);
            return output.ToArray();
        }
    }

    private static Dictionary<string, object> Identity(Bitmap bitmap, byte[] rgba)
    {
        return new Dictionary<string, object> {
            { "width", bitmap.Width }, { "height", bitmap.Height }, { "rgba_sha256", Hash(rgba) }
        };
    }

    private static Dictionary<string, object> Read(Dictionary<string, object> request)
    {
        byte[] source = File.ReadAllBytes((string)request["path"]);
        using (Bitmap decoded = Tlg(source))
        {
            var result = Identity(decoded, Rgba(decoded));
            result["file_sha256"] = Hash(source);
            result["key"] = request["key"];
            return result;
        }
    }

    private static Dictionary<string, object> Encode(Dictionary<string, object> request)
    {
        string original = (string)request["original"], png = (string)request["png"], target = (string)request["target"];
        if (String.Equals(original, target, StringComparison.OrdinalIgnoreCase) || String.Equals(png, target, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Output must differ from source files");
        if (File.Exists(target)) throw new IOException("Output already exists: " + target);
        byte[] source = File.ReadAllBytes(original), input = File.ReadAllBytes(png);
        using (Bitmap baseline = Tlg(source))
        using (var stream = new MemoryStream(input, false))
        using (var edited = new Bitmap(stream))
        {
            if (baseline.Width != edited.Width || baseline.Height != edited.Height)
                throw new InvalidDataException("TLG dimensions differ: " + png);
            byte[] rgba = Rgba(edited), before = Rgba(baseline);
            if (request.ContainsKey("expected_rgba_sha256") && Hash(rgba) != (string)request["expected_rgba_sha256"])
                throw new InvalidDataException("PNG RGBA differs between independent decoders or input changed: " + png);
            bool changed = !rgba.SequenceEqual(before);
            bool encoded = changed || (request.ContainsKey("force") && (bool)request["force"]);
            byte[] output = source;
            bool wrapped;
            byte[] trailer;
            byte[] rawSource = RawTlg(source, out wrapped, out trailer);
            int version = rawSource.Length >= 6 && rawSource[3] == (byte)'5' ? 5 : rawSource.Length >= 6 && rawSource[3] == (byte)'6' ? 6 : 0;
            if (version == 0) throw new InvalidDataException("Unsupported TLG signature: " + original);
            if (encoded)
                output = PreserveShell((byte[])(version == 5 ? Encode5 : Encode6).Invoke(null, new object[] { edited }), wrapped, trailer);
            using (Bitmap verify = Tlg(output))
            {
                byte[] actual = Rgba(verify);
                if (verify.Width != edited.Width || verify.Height != edited.Height || !actual.SequenceEqual(rgba))
                    throw new InvalidDataException("TLG full RGBA comparison failed: " + png);
            }
            if (!File.ReadAllBytes(original).SequenceEqual(source))
                throw new InvalidDataException("TLG source changed during encoding");
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            using (var outputStream = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                outputStream.Write(output, 0, output.Length);
            var result = Identity(edited, rgba);
            result["changed"] = changed; result["codec"] = "TLG" + version;
            result["encoded"] = encoded;
            result["file_sha256"] = Hash(output); result["source_sha256"] = Hash(source);
            result["lossless"] = true;
            return result;
        }
    }

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length != 2) throw new ArgumentException("request.json result.json required");
            var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 100 };
            var request = serializer.Deserialize<Dictionary<string, object>>(File.ReadAllText(args[0], Encoding.UTF8));
            string libraryPath = Path.GetFullPath((string)request["library"]);
            AppDomain.CurrentDomain.AssemblyResolve += (sender, eventArgs) => {
                string path = Path.Combine(Path.GetDirectoryName(libraryPath), new AssemblyName(eventArgs.Name).Name + ".dll");
                return File.Exists(path) ? Assembly.LoadFrom(path) : null;
            };
            Type native = Assembly.LoadFrom(libraryPath).GetType("FreeMote.Tlg.TlgNative", true);
            Decode = native.GetMethod("ToBitmap", new Type[] { typeof(byte[]), typeof(int).MakeByRefType(), typeof(bool) });
            Encode5 = native.GetMethod("ToTlg5", new Type[] { typeof(Bitmap) });
            Encode6 = native.GetMethod("ToTlg6", new Type[] { typeof(Bitmap) });
            if (Decode == null || Encode5 == null || Encode6 == null) throw new MissingMethodException("TlgLib codec API missing");
            string mode = (string)request["mode"];
            var results = new List<Dictionary<string, object>>();
            foreach (Dictionary<string, object> item in (ArrayList)request["items"])
            {
                try
                {
                    if (mode == "read") results.Add(Read(item));
                    else if (mode == "tlg") results.Add(Encode(item));
                    else throw new ArgumentException("Unknown mode");
                }
                catch (Exception error)
                {
                    while (error is TargetInvocationException && error.InnerException != null) error = error.InnerException;
                    results.Add(new Dictionary<string, object> { { "error", error.ToString() } });
                }
            }
            byte[] json = new UTF8Encoding(false).GetBytes(serializer.Serialize(results));
            using (var output = new FileStream(args[1], FileMode.CreateNew, FileAccess.Write, FileShare.None)) output.Write(json, 0, json.Length);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
