Name:           dnotool
Version:        0.5.9
Release:        1%{?dist}
Summary:        Network device management tool for MOS/ALT Linux
Group:          System/Configuration/Other

License:        Proprietary
URL:            https://github.com/xp9k/dno-tool
Source0:        %{name}-%{version}.tar.gz

BuildArch:      x86_64

%define __os_install_post %{nil}
%define __strip /bin/true
%define __spec_install_post /bin/true

Requires:       polkit
Requires:       openssh-clients

%description
DNO Tool is a GUI application for remote administration of computers
running MOS/ALT Linux and Windows via SSH protocol.

Features:
- Mass SSH command execution on dozens of hosts simultaneously
- 150+ ready-made command library
- SSH key management with automatic deployment
- SFTP file manager
- Network monitoring (ping, port scanning)
- Remote desktop recording via FFmpeg/VLC

%prep
%setup -q -n %{name}-%{version}

%install
rm -rf %{buildroot}

install -Dm755 dnotool                                           %{buildroot}%{_bindir}/dnotool
install -Dm755 policykit/dnotool-admin                          %{buildroot}%{_bindir}/dnotool-admin
install -Dm644 policykit/com.dnotool.policy                    %{buildroot}%{_datadir}/polkit-1/actions/com.dnotool.policy
install -Dm644 policykit/com.dnotool.desktop                   %{buildroot}%{_datadir}/applications/com.dnotool.desktop
install -Dm644 icon/dnotool.png                                %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/dnotool.png
install -Dm644 icon/dnotool.svg                                %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/dnotool.svg

%post
update-desktop-database %{_datadir}/applications 2>/dev/null || :
gtk-update-icon-cache -f %{_datadir}/icons/hicolor 2>/dev/null || :

%postun
update-desktop-database %{_datadir}/applications 2>/dev/null || :
if [ $1 -eq 0 ]; then
    gtk-update-icon-cache -f %{_datadir}/icons/hicolor 2>/dev/null || :
fi

%files
%defattr(-,root,root,-)
%{_bindir}/dnotool
%{_bindir}/dnotool-admin
%{_datadir}/polkit-1/actions/com.dnotool.policy
%{_datadir}/applications/com.dnotool.desktop
%{_datadir}/icons/hicolor/256x256/apps/dnotool.png
%{_datadir}/icons/hicolor/scalable/apps/dnotool.svg