using System;
using System.Diagnostics;
using System.Linq;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Threading.Tasks;

namespace AirMaster7pConnect.ViewModels;

public class MainViewModel : BaseViewModel
{
    private string _ssid = string.Empty;
    private string _bssid = string.Empty;
    private string _password = string.Empty;
    private object? _content;

    public string Ssid
    {
        get => _ssid;
        set => SetField(ref _ssid, value);
    }

    public string Bssid
    {
        get => _bssid;
        set => SetField(ref _bssid, value);
    }

    public string Password
    {
        get => _password;
        set => SetField(ref _password, value);
    }

    public object? Content => _content;

    public MainViewModel()
    {
        UseCurrentConnection();

        var vm = new ListenViewModel();
        _content = vm;
        vm.StartReceive();
    }

    public ValueTask SetContentAsync(object? content)
    {
        var oldContent = _content;
        if (SetField(ref _content, content, nameof(Content)))
            return (oldContent as IAsyncDisposable)?.DisposeAsync() ?? ValueTask.CompletedTask;

        return ValueTask.CompletedTask;
    }
    
    public async void Connect()
    {
        if (!PhysicalAddress.TryParse(Bssid.Replace(':', '-'), out var bssid))
        {
            await SetContentAsync("BSSID format invalid (example: 01:02:03:04:05:06)");
            return;
        }
        
        var wifiInterface = GetWifiInterface();
        if (wifiInterface == null)
        {
            await SetContentAsync("Cannot find any available WiFi adapter");
            return;
        }
        
        var localAddress = wifiInterface.GetIPProperties()
            .UnicastAddresses
            .Where(x => x.Address.AddressFamily == AddressFamily.InterNetwork)
            .Select(x => x.Address)
            .FirstOrDefault();
        
        if(localAddress == null)
        {
            await SetContentAsync($"Cannot find IPv4 address for WiFi interface: {wifiInterface.Name}");
            return;
        }

        var vm = new ConnectionViewModel(this);
        await SetContentAsync(vm);
        vm.StartConnect(Ssid, bssid, Password, localAddress);
    }

    public async void UseCurrentConnection()
    {
        try
        {
            var process = new Process();
            process.StartInfo.FileName = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport";
            process.StartInfo.Arguments = "-I";
            process.StartInfo.RedirectStandardOutput = true;
            process.StartInfo.UseShellExecute = false;
            process.Start();
            var output = await process.StandardOutput.ReadToEndAsync();
            await process.WaitForExitAsync();

            foreach (var line in output.Split('\n'))
            {
                var trimmed = line.Trim();
                if (trimmed.StartsWith("SSID:"))
                    Ssid = trimmed.Substring(5).Trim();
                else if (trimmed.StartsWith("BSSID:"))
                    Bssid = trimmed.Substring(6).Trim();
            }
        }
        catch
        {
            await SetContentAsync("Could not detect WiFi. Fill in fields manually.");
        }
    }

    private static NetworkInterface? GetWifiInterface()
    {
        var adapters = NetworkInterface.GetAllNetworkInterfaces();
        return adapters.FirstOrDefault(x => x is { NetworkInterfaceType: NetworkInterfaceType.Wireless80211, OperationalStatus: OperationalStatus.Up, IsReceiveOnly: false });
    }
}